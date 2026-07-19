"""Evidence-grounded report interpretation with optional LLM-assisted narration."""

from __future__ import annotations

from collections import defaultdict
import json
import math
import re
import time
from typing import TYPE_CHECKING, Callable, Iterable

import requests

import codex_chatgpt
from codex_chatgpt import (
    CODEX_ACCOUNT_MODEL,
    CODEX_DEFAULT_REASONING,
    CODEX_DEFAULT_SPEED,
    PROVIDER_CODEX_CHATGPT,
)

if TYPE_CHECKING:
    from rice_gene_core import AnalysisBundle


MODE_RULES = "rules"
MODE_LLM = "llm"
PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI_COMPATIBLE = "openai_compatible"
_RICE_IDENTIFIER = re.compile(r"(?:LOC_Os\d{2}g\d{5}(?:\.\d+)?|Os\d{2}[gt]\d{7}(?:-\d+)?)")


def probe_model_connection(
    *,
    provider: str,
    base_url: str,
    model: str,
    api_key: str = "",
    session: requests.Session | None = None,
    timeout: int = 30,
) -> str:
    """Verify one configured model endpoint with a fixed, data-free request."""
    normalized = base_url.strip().rstrip("/")
    selected_model = model.strip()
    if not normalized or not selected_model:
        raise ValueError("请先填写模型服务地址和模型名称。")
    client = session or requests.Session()
    started = time.monotonic()
    if provider == PROVIDER_OLLAMA:
        url = normalized if normalized.endswith("/api/chat") else normalized + "/api/chat"
        response = client.post(
            url,
            json={
                "model": selected_model,
                "stream": False,
                "messages": [{"role": "user", "content": "Reply with OK."}],
                "options": {"temperature": 0, "num_predict": 8},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        content = str(response.json().get("message", {}).get("content") or "").strip()
    elif provider == PROVIDER_OPENAI_COMPATIBLE:
        if not api_key.strip():
            raise ValueError("请先填写 API Key。")
        url = normalized if normalized.endswith("/chat/completions") else normalized + "/chat/completions"
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"},
            json={
                "model": selected_model,
                "temperature": 0,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "Reply with OK."}],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        choices = response.json().get("choices") or []
        content = str(choices[0].get("message", {}).get("content") if choices else "").strip()
    else:
        raise ValueError(f"不支持的大模型提供方：{provider}")
    if not content:
        raise ValueError("模型服务已响应，但没有返回内容。")
    elapsed = time.monotonic() - started
    return f"{selected_model} · {elapsed:.1f} 秒"


def _as_float(value: object) -> float | None:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def _join(values: Iterable[object], limit: int = 6) -> str:
    unique = list(dict.fromkeys(str(value).strip() for value in values if str(value or "").strip()))
    return "、".join(unique[:limit]) + (f"等{len(unique)}项" if len(unique) > limit else "")


def _interpretation_row(
    section: str,
    title: str,
    interpretation: str,
    evidence_basis: str,
    evidence_level: str,
    confidence: str,
    limitations: str,
    recommended_action: str,
    source_refs: str = "",
) -> dict[str, object]:
    return {
        "section": section,
        "title": title,
        "interpretation": interpretation,
        "evidence_basis": evidence_basis,
        "evidence_level": evidence_level,
        "confidence": confidence,
        "limitations": limitations,
        "recommended_action": recommended_action,
        "source_refs": source_refs,
    }


def _multiomics_interpretations(bundle: "AnalysisBundle") -> list[dict[str, object]]:
    rows = list(bundle.lab_omics_differential)
    if not rows and not bundle.lab_omics_profiles:
        return [
            _interpretation_row(
                "lab_omics",
                "实验室多组学解读",
                "当前基因未命中可解读的实验室多组学差异或项目内定量记录，不能据此判断处理响应方向。",
                f"差异记录 0 条；项目内定量记录 {len(bundle.lab_omics_profiles)} 条。",
                "数据缺口",
                "不适用",
                "可能是数据库尚未解锁、ID 未映射，或当前基因未进入已纳入的数据集。",
                "先核对 MSU locus、授权解锁状态和数据集覆盖范围；如有原始或已分析结果，可补充导入后重新解读。",
            )
        ]

    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        locus = str(row.get("msu_locus") or row.get("input_id") or "未标注基因")
        grouped[locus].append(row)
    if not grouped and bundle.lab_omics_profiles:
        for row in bundle.lab_omics_profiles:
            grouped[str(row.get("msu_locus") or "未标注基因")]

    interpretations: list[dict[str, object]] = []
    for locus, gene_rows in list(grouped.items())[:20]:
        numeric = [(row, _as_float(row.get("log2fc"))) for row in gene_rows]
        numeric = [(row, value) for row, value in numeric if value is not None]
        assays = list(dict.fromkeys(str(row.get("assay") or row.get("feature_type") or "未标注组学") for row in gene_rows))
        datasets = list(dict.fromkeys(str(row.get("dataset_name") or row.get("dataset_id") or "") for row in gene_rows if row.get("dataset_name") or row.get("dataset_id")))
        positives = [(row, value) for row, value in numeric if value > 0]
        negatives = [(row, value) for row, value in numeric if value < 0]
        descriptive_count = sum(bool(row.get("descriptive")) for row in gene_rows)
        tested = []
        supported = []
        for row, _ in numeric:
            pvalue = _as_float(row.get("padj"))
            if pvalue is None:
                pvalue = _as_float(row.get("pvalue"))
            if pvalue is not None:
                tested.append(pvalue)
                if pvalue <= 0.05 and not row.get("descriptive"):
                    supported.append(pvalue)

        strongest_row: dict[str, object] = {}
        strongest_value: float | None = None
        if numeric:
            strongest_row, strongest_value = max(numeric, key=lambda item: abs(item[1]))
        context = "；".join(
            value for value in [
                str(strongest_row.get("comparison_name") or strongest_row.get("treatment") or ""),
                str(strongest_row.get("time_label") or ""),
                str(strongest_row.get("assay") or strongest_row.get("feature_type") or ""),
            ] if value
        )

        if positives and negatives:
            direction = "不同处理、时间点或组学层之间同时出现上调和下调，提示响应具有场景依赖性"
        elif positives:
            direction = "现有差异记录整体以上调为主"
        elif negatives:
            direction = "现有差异记录整体以下调为主"
        elif numeric:
            direction = "现有变化接近零或方向不明确"
        else:
            direction = "当前记录缺少可解析的 log2FC，不能判断响应方向"

        lower_assays = " ".join(assays).casefold()
        has_transcript = any(token in lower_assays for token in ("rna", "transcript", "microarray", "mrna", "芯片"))
        has_protein = any(token in lower_assays for token in ("protein", "proteome", "总蛋白"))
        has_ptm = any(
            row.get("site_position") is not None
            or str(row.get("feature_type") or "").casefold() in {"phosphosite", "ubiquitination_site"}
            or any(token in str(row.get("assay") or "").casefold() for token in ("phosph", "ubiquit", "磷酸", "泛素"))
            for row in gene_rows
        )
        layer_note = []
        if has_transcript and has_protein:
            layer_note.append("同时覆盖转录与蛋白层，可用于检查方向是否一致")
        if has_ptm:
            layer_note.append("检测到PTM位点层响应；PTM变化不能等同于总蛋白丰度变化")
        if len(assays) == 1:
            layer_note.append("目前主要来自单一组学层，跨层机制链仍不完整")

        strongest = ""
        if strongest_value is not None:
            strongest = f"绝对变化最大的记录为 {context or '未标注比较'}（log2FC={strongest_value:.3f}）"
        significance = (
            f"{len(supported)}/{len(tested)} 条具有可解析统计量的记录达到 P/adjusted P≤0.05"
            if tested else "当前记录没有统一可比较的统计显著性字段"
        )
        interpretation = "；".join(
            value for value in [f"{locus}：{direction}", strongest, *layer_note, significance] if value
        ) + "。这些结果支持“处理相关候选”定位，但不能单独证明因果机制。"
        confidence = "中" if len(datasets) >= 2 or len(assays) >= 2 else "低-中"
        if descriptive_count == len(gene_rows):
            confidence = "低"
        recommendation = (
            "优先在变化最大的处理/时间点复核原始定量与重复；用 qRT-PCR 和免疫学/靶向质谱进行正交验证。"
            + ("对响应PTM位点开展PRM、位点突变和蛋白稳定性/互作验证。" if has_ptm else "")
            + ("若转录与蛋白方向不一致，进一步检查翻译效率、蛋白降解和取样时间差。" if has_transcript and has_protein else "")
        )
        interpretations.append(
            _interpretation_row(
                "lab_omics",
                f"{locus} 多组学响应解读",
                interpretation,
                (
                    f"{len(gene_rows)} 条差异记录；{len(datasets)} 个数据集；"
                    f"组学层：{_join(assays)}；上调 {len(positives)} 条、下调 {len(negatives)} 条；"
                    f"描述性记录 {descriptive_count} 条。"
                ),
                "组学支持（非因果）",
                confidence,
                "不同项目仅比较各自已有 log2FC；原始丰度不可跨组学直接比较。样本背景、时间点、重复和统计方法不一致时不能合并为一个效应量。"
                + ("PTM位点丰度可同时受修饰化学计量和总蛋白丰度影响，未经总蛋白归一化不能直接推断修饰酶活性。" if has_ptm else ""),
                recommendation,
                _join(datasets, limit=10),
            )
        )
    return interpretations


def _subgroup_patterns(haplotypes: list[dict[str, object]]) -> list[str]:
    patterns: list[tuple[float, str]] = []
    for row in haplotypes:
        haplotype = str(row.get("haplotype") or "")
        for token in str(row.get("subgroup_frequency") or "").split(";"):
            group, separator, fraction = token.partition(":")
            numerator, slash, denominator = fraction.partition("/")
            if not separator or not slash:
                continue
            try:
                frequency = int(numerator) / int(denominator)
            except (ValueError, ZeroDivisionError):
                continue
            patterns.append((frequency, f"{haplotype} 在 {group} 中为 {int(numerator)}/{int(denominator)}（{frequency:.1%}）"))
    return [text for _, text in sorted(patterns, reverse=True)[:3]]


def _haplotype_interpretations(bundle: "AnalysisBundle") -> list[dict[str, object]]:
    if not bundle.haplotypes:
        reason = (
            "已有变异记录，但没有包含可用样本 GT 的基因型矩阵，因而不能从数据库变异列表推断单倍型。"
            if bundle.variants else
            "当前没有可解析变异和样本 GT，无法进行单倍型划分。"
        )
        return [
            _interpretation_row(
                "haplotype",
                "单倍型分析解读",
                reason,
                f"变异 {len(bundle.variants)} 个；可计算单倍型 0 个。",
                "数据缺口",
                "不适用",
                "单倍型必须由同一批样本在通过REF、缺失率和MAF过滤的位点上联合计算，不能用孤立变异列表代替。",
                "上传含 GT 样本列的 IRGSP-1.0 VCF；如需群体分层解读，同时提供 sample/group 对照表和目标表型。",
            )
        ]

    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in bundle.haplotypes:
        grouped[str(row.get("input_id") or row.get("rap_gene") or "当前基因")].append(row)
    output: list[dict[str, object]] = []
    for gene, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: (-int(row.get("sample_count") or 0), str(row.get("haplotype") or "")))
        total = sum(int(row.get("sample_count") or 0) for row in ordered)
        frequencies = [_as_float(row.get("sample_frequency")) for row in ordered]
        normalized = [value for value in frequencies if value is not None and 0 <= value <= 1]
        major = ordered[0]
        major_frequency = _as_float(major.get("sample_frequency"))
        if major_frequency is None and total:
            major_frequency = int(major.get("sample_count") or 0) / total
        diversity = 1 - sum(value * value for value in normalized) if normalized else None
        filtered_sites = max(int(row.get("filtered_variant_count") or 0) for row in ordered)
        if major_frequency is not None and major_frequency >= 0.70:
            structure = "存在占明显优势的主单倍型，群体结构相对集中"
        elif major_frequency is not None and major_frequency >= 0.40:
            structure = "存在常见主单倍型，同时保留一定单倍型多样性"
        else:
            structure = "未见单一单倍型占绝对优势，群体单倍型较分散"
        group_patterns = _subgroup_patterns(ordered)
        group_text = (
            "；群体分层中较高的观察频率包括：" + "；".join(group_patterns)
            if group_patterns else
            "；未提供可用 sample/group 对照，不能判断群体或材料背景富集"
        )
        if major_frequency is not None:
            interpretation = (
                f"{gene} 由 {filtered_sites} 个通过过滤的位点划分出 {len(ordered)} 个单倍型，"
                f"共覆盖 {total} 个可分型样本。主单倍型 {major.get('haplotype') or 'H1'}"
                f" 占 {major_frequency:.1%}，{structure}"
            )
        else:
            interpretation = (
                f"{gene} 由 {filtered_sites} 个通过过滤的位点划分出 {len(ordered)} 个单倍型，"
                f"共覆盖 {total} 个可分型样本，频率字段不完整"
            )
        interpretation += group_text + "。该结果描述群体分布，不等同于性状关联或功能效应。"
        diversity_text = f"{diversity:.3f}" if diversity is not None else "未计算"
        output.append(
            _interpretation_row(
                "haplotype",
                f"{gene} 单倍型结构解读",
                interpretation,
                (
                    f"单倍型 {len(ordered)} 个；可分型样本 {total} 个；过滤位点 {filtered_sites} 个；"
                    f"主单倍型 {major.get('haplotype') or 'H1'}；多样性指数 1−Σp²={diversity_text}。"
                ),
                "群体基因型结构（非性状因果）",
                "中" if group_patterns and total >= 20 else "低-中",
                "当前没有把单倍型与抗虫、抗病、表达量或其他表型进行统计关联；群体分层、亲缘关系和样本量可能造成表观富集。",
                "选择主单倍型和频率较高的次要单倍型代表材料，先复核关键位点；随后结合表型进行群体结构校正的关联检验，并用等位基因材料、互补或编辑实验验证。",
                str(major.get("source_url") or ""),
            )
        )
    return output


def build_rule_interpretations(bundle: "AnalysisBundle") -> list[dict[str, object]]:
    """Return deterministic, auditable interpretations without inventing biology."""
    rows = [*_multiomics_interpretations(bundle), *_haplotype_interpretations(bundle)]
    direct = len(bundle.genetic_evidence)
    literature = len(bundle.literature_rows)
    omics_available = bool(bundle.lab_omics_differential or bundle.lab_omics_profiles)
    haplotypes_available = bool(bundle.haplotypes)
    if direct:
        evidence_statement = f"已有 {direct} 条数据库/人工遗传或功能证据，可作为解释起点"
    elif literature:
        evidence_statement = f"检索到 {literature} 篇关联文献，但仍需逐篇核验全文与当前基因的直接关系"
    else:
        evidence_statement = "尚无可直接支撑功能结论的遗传证据或关联文献"
    support = []
    if omics_available:
        support.append("实验室多组学提供处理响应线索")
    if haplotypes_available:
        support.append("样本GT支持单倍型结构描述")
    if bundle.efp_rows:
        support.append("eFP提供组织/处理表达背景")
    support_text = "；".join(support) if support else "当前主要是序列与计算预测结果"
    rows.insert(
        0,
        _interpretation_row(
            "overall",
            "报告执行摘要",
            f"{evidence_statement}；{support_text}。因此本报告适合用于提出候选假设和安排验证优先级，不应直接写成已证实机制。",
            (
                f"遗传/功能证据 {direct} 条；关联文献 {literature} 篇；eFP {len(bundle.efp_rows)} 条；"
                f"实验室多组学差异 {len(bundle.lab_omics_differential)} 条；单倍型 {len(bundle.haplotypes)} 个。"
            ),
            "规则化综合判断",
            "中" if direct and (omics_available or haplotypes_available) else "低-中",
            "证据来自不同数据库、项目与计算模块；缺失全文、原始重复或表型时不能闭合因果链。",
            "先验证同时得到直接证据与独立组学支持的场景；没有直接证据时，优先补充表达、遗传材料和目标处理表型。",
        ),
    )
    return rows


def _deidentification_map(bundle: "AnalysisBundle") -> dict[str, str]:
    """Map user-defined labels to stable per-request aliases before cloud transfer."""
    replacements: dict[str, str] = {}
    counters: defaultdict[str, int] = defaultdict(int)

    def register(value: object, category: str) -> None:
        raw = str(value or "").strip()
        if not raw or _RICE_IDENTIFIER.fullmatch(raw) or raw in replacements:
            return
        counters[category] += 1
        replacements[raw] = f"{category}_{counters[category]}"

    for value in bundle.inputs:
        register(value, "input")
    for row in bundle.lab_omics_differential:
        register(row.get("msu_locus"), "gene")
        register(row.get("input_id"), "gene")
        register(row.get("dataset_name") or row.get("dataset_id"), "dataset")
        register(row.get("comparison_name"), "comparison")
        register(row.get("treatment"), "treatment")
        register(row.get("time_label"), "timepoint")
    for row in bundle.haplotypes:
        register(row.get("input_id"), "input")
        register(row.get("rap_gene"), "gene")
        for token in str(row.get("subgroup_frequency") or "").split(";"):
            group, separator, _ = token.partition(":")
            if separator:
                register(group, "subgroup")
    return replacements


def _deidentify_text(value: object, replacements: dict[str, str]) -> str:
    text = str(value or "")
    for raw in sorted(replacements, key=len, reverse=True):
        text = text.replace(raw, replacements[raw])
    text = re.sub(r"(?<!\w)(?:[A-Za-z]:[\\/]|/)[^\s;，。]+", "[local_path]", text)
    text = re.sub(r"(?i)\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{8,}\b", "[redacted_secret]", text)
    return text


def _llm_payload(bundle: "AnalysisBundle", rule_rows: list[dict[str, object]]) -> dict[str, object]:
    replacements = _deidentification_map(bundle)
    multiomics = sorted(
        bundle.lab_omics_differential,
        key=lambda row: -abs(_as_float(row.get("log2fc")) or 0),
    )[:30]
    safe_multiomics = []
    for row in multiomics:
        safe_row = {
            key: row.get(key)
            for key in (
                "msu_locus", "comparison_name", "treatment", "time_label", "assay", "feature_type",
                "log2fc", "pvalue", "padj", "regulated", "site_position", "site_residue", "dataset_name",
            )
        }
        safe_row["descriptive"] = bool(row.get("descriptive"))
        safe_multiomics.append(
            {key: _deidentify_text(value, replacements) if isinstance(value, str) else value for key, value in safe_row.items()}
        )
    safe_haplotypes = []
    for row in bundle.haplotypes[:20]:
        subgroup_entries = [token for token in str(row.get("subgroup_frequency") or "").split(";") if token.strip()]
        safe_haplotypes.append(
            {
                "input_id": _deidentify_text(row.get("input_id"), replacements),
                "rap_gene": _deidentify_text(row.get("rap_gene"), replacements),
                "haplotype": _deidentify_text(row.get("haplotype"), replacements),
                "sample_count": row.get("sample_count"),
                "sample_frequency": row.get("sample_frequency"),
                "subgroup_entry_count": len(subgroup_entries),
                "filtered_variant_count": row.get("filtered_variant_count"),
            }
        )
    safe_rule_rows = [
        {
            key: _deidentify_text(value, replacements) if isinstance(value, str) else value
            for key, value in row.items()
            if key != "source_refs"
        }
        for row in rule_rows
    ]
    safe_analysis_objects = []
    for index, value in enumerate(bundle.inputs, 1):
        text = str(value or "").strip()
        if _RICE_IDENTIFIER.fullmatch(text):
            safe_analysis_objects.append(text)
        else:
            safe_analysis_objects.append(f"input_{index}")
    return {
        "analysis_object": safe_analysis_objects,
        "analysis_object_count": len(bundle.inputs),
        "rule_interpretations": safe_rule_rows,
        "multiomics_top_records": safe_multiomics,
        "haplotype_summary": safe_haplotypes,
        "evidence_counts": {
            "genetic_evidence": len(bundle.genetic_evidence),
            "literature": len(bundle.literature_rows),
            "efp": len(bundle.efp_rows),
            "variants": len(bundle.variants),
        },
        "privacy_note": "No raw sequence, individual sample name, password, token, key, or source file path is included.",
    }


def _extract_json(text: str) -> dict[str, object]:
    cleaned = text.strip()
    fence = re.search(r"\x60\x60\x60(?:json)?\s*(\{.*\})\s*\x60\x60\x60", cleaned, re.S)
    if fence:
        cleaned = fence.group(1)
    else:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start:end + 1]
    parsed = json.loads(cleaned)
    if not isinstance(parsed, dict):
        raise ValueError("模型未返回 JSON 对象")
    return parsed


def _llm_rows(payload: dict[str, object]) -> list[dict[str, object]]:
    output = []
    for key, section, title in (
        ("executive_summary", "ai_overall", "AI增强执行摘要"),
        ("multiomics_interpretation", "ai_lab_omics", "AI增强多组学解读"),
        ("haplotype_interpretation", "ai_haplotype", "AI增强单倍型解读"),
    ):
        value = str(payload.get(key) or "").strip()
        if value:
            output.append(
                _interpretation_row(
                    section, title, value,
                    "仅基于本报告已整理的结构化证据和规则化解读。",
                    "AI辅助推断", "待人工核验",
                    "大模型可能遗漏证据边界或产生不恰当联想；任何新增机制表述必须回到原始数据和全文核验。",
                    "由研究人员逐句核对证据来源、方向、处理背景和实验可行性后再用于论文或实验设计。",
                )
            )
    hypotheses = payload.get("integrated_hypotheses")
    if isinstance(hypotheses, list):
        for index, item in enumerate(hypotheses[:5], 1):
            if not isinstance(item, dict):
                continue
            hypothesis = str(item.get("hypothesis") or "").strip()
            if not hypothesis:
                continue
            output.append(
                _interpretation_row(
                    "ai_overall",
                    f"AI候选假设 {index}",
                    hypothesis,
                    str(item.get("support") or "模型未明确列出支持证据。"),
                    "AI辅助推断", "待人工核验",
                    str(item.get("limitations") or "尚未形成因果证据链。"),
                    str(item.get("experiment") or "需要设计区分性实验验证。"),
                )
            )
    return output


def _llm_prompt(bundle: "AnalysisBundle", rule_rows: list[dict[str, object]]) -> tuple[str, str]:
    evidence = json.dumps(_llm_payload(bundle, rule_rows), ensure_ascii=False, separators=(",", ":"))
    system = (
        "你是植物分子生物学科研报告助手。只能使用用户提供的结构化证据，不得补写数据库外事实、"
        "不得把相关性或计算预测写成因果。请区分已有证据、组学支持、合理推测和需实验验证。"
        "重点解释多组学方向、跨层一致/不一致、PTM边界，以及单倍型频率、群体分层与性状关联缺口。"
        "不得调用工具、读取本机文件、联网搜索或引用结构化证据以外的内容。"
        "返回严格JSON，字段为 executive_summary、multiomics_interpretation、haplotype_interpretation、"
        "integrated_hypotheses；最后一项是对象数组，每个对象含 hypothesis、support、limitations、experiment。"
    )
    return system, "请解读以下 My Bio Tools 结构化结果：\n" + evidence


def request_llm_interpretations(
    bundle: "AnalysisBundle",
    rule_rows: list[dict[str, object]],
    *,
    provider: str,
    base_url: str,
    model: str,
    api_key: str = "",
    session: requests.Session | None = None,
    timeout: int = 180,
) -> list[dict[str, object]]:
    """Ask a configured model to narrate evidence; never replace deterministic rows."""
    if not model.strip():
        raise ValueError("大模型名称不能为空。")
    system, prompt = _llm_prompt(bundle, rule_rows)
    client = session or requests.Session()
    normalized = base_url.strip().rstrip("/")
    if provider == PROVIDER_OLLAMA:
        url = normalized if normalized.endswith("/api/chat") else normalized + "/api/chat"
        response = client.post(
            url,
            json={
                "model": model.strip(),
                "stream": False,
                "format": "json",
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                "options": {"temperature": 0.1},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        content = str(response.json().get("message", {}).get("content") or "")
    elif provider == PROVIDER_OPENAI_COMPATIBLE:
        if not api_key.strip():
            raise ValueError("云端大模型需要 API Key。")
        url = normalized if normalized.endswith("/chat/completions") else normalized + "/chat/completions"
        response = client.post(
            url,
            headers={"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"},
            json={
                "model": model.strip(),
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            },
            timeout=timeout,
        )
        response.raise_for_status()
        choices = response.json().get("choices") or []
        content = str(choices[0].get("message", {}).get("content") if choices else "")
    else:
        raise ValueError(f"不支持的大模型提供方：{provider}")
    if not content.strip():
        raise ValueError("大模型返回了空内容。")
    rows = _llm_rows(_extract_json(content))
    if not rows:
        raise ValueError("大模型未返回可用的解读字段。")
    return rows


def request_codex_chatgpt_interpretations(
    bundle: "AnalysisBundle",
    rule_rows: list[dict[str, object]],
    *,
    model: str = CODEX_ACCOUNT_MODEL,
    reasoning_effort: str = CODEX_DEFAULT_REASONING,
    speed: str = CODEX_DEFAULT_SPEED,
    is_cancelled: Callable[[], bool] | None = None,
    timeout: int = codex_chatgpt.CODEX_TIMEOUT_SECONDS,
) -> tuple[list[dict[str, object]], codex_chatgpt.CodexRunResult]:
    """Use the local Codex CLI with ChatGPT authentication and no agent tools."""
    system, prompt = _llm_prompt(bundle, rule_rows)
    result = codex_chatgpt.run_codex_interpretation(
        f"{system}\n\n{prompt}",
        model=model,
        reasoning_effort=reasoning_effort,
        speed=speed,
        timeout=timeout,
        is_cancelled=is_cancelled,
    )
    rows = _llm_rows(result.payload)
    if not rows:
        raise codex_chatgpt.CodexClientError("invalid_output", "ChatGPT/Codex 未返回可用的解读字段。")
    return rows, result


def generate_interpretations(
    bundle: "AnalysisBundle",
    *,
    mode: str = MODE_RULES,
    provider: str = "",
    base_url: str = "",
    model: str = "",
    api_key: str = "",
    codex_reasoning: str = CODEX_DEFAULT_REASONING,
    codex_speed: str = CODEX_DEFAULT_SPEED,
    session: requests.Session | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    rules = build_rule_interpretations(bundle)
    selected_codex_model = model or CODEX_ACCOUNT_MODEL
    status: dict[str, object] = {
        "requested_mode": mode,
        "effective_mode": MODE_RULES,
        "provider": provider if mode == MODE_LLM else "",
        "provider_label": "",
        "model": selected_codex_model if mode == MODE_LLM and provider == PROVIDER_CODEX_CHATGPT else (model if mode == MODE_LLM else ""),
        "model_label": codex_chatgpt.codex_model_label(selected_codex_model) if mode == MODE_LLM and provider == PROVIDER_CODEX_CHATGPT else "",
        "reasoning_effort": codex_reasoning if mode == MODE_LLM and provider == PROVIDER_CODEX_CHATGPT else "",
        "reasoning_label": codex_chatgpt.codex_reasoning_label(codex_reasoning) if mode == MODE_LLM and provider == PROVIDER_CODEX_CHATGPT else "",
        "speed": codex_speed if mode == MODE_LLM and provider == PROVIDER_CODEX_CHATGPT else "",
        "speed_label": codex_chatgpt.codex_speed_label(codex_speed) if mode == MODE_LLM and provider == PROVIDER_CODEX_CHATGPT else "",
        "client_version": "",
        "rule_section_count": len(rules),
        "ai_section_count": 0,
        "error_code": "",
        "error": "",
        "privacy": (
            "离线规则解读未向外部模型发送数据。"
            if mode != MODE_LLM
            else "模型仅接收结构化摘要；不发送原始序列、样本名、密码、令牌、密钥或源文件路径。"
        ),
    }
    if mode != MODE_LLM:
        return rules, status
    try:
        if provider == PROVIDER_CODEX_CHATGPT:
            ai_rows, result = request_codex_chatgpt_interpretations(
                bundle,
                rules,
                model=selected_codex_model,
                reasoning_effort=codex_reasoning,
                speed=codex_speed,
                is_cancelled=is_cancelled,
            )
            status["provider_label"] = "ChatGPT 账号（Codex）"
            status["client_version"] = result.client_version
        else:
            ai_rows = request_llm_interpretations(
                bundle,
                rules,
                provider=provider,
                base_url=base_url,
                model=model,
                api_key=api_key,
                session=session,
            )
            status["provider_label"] = "本机 Ollama" if provider == PROVIDER_OLLAMA else "OpenAI 兼容云端 API"
        status["effective_mode"] = MODE_LLM
        status["ai_section_count"] = len(ai_rows)
        return [*rules, *ai_rows], status
    except codex_chatgpt.CodexInvocationCancelled:
        raise
    except codex_chatgpt.CodexClientError as exc:
        status["client_version"] = exc.client_version
        status["error_code"] = exc.code
        status["error"] = exc.user_message
        return rules, status
    except Exception as exc:
        status["error_code"] = "provider_error"
        status["error"] = f"{type(exc).__name__}: {exc}"
        return rules, status


__all__ = [
    "MODE_LLM",
    "MODE_RULES",
    "PROVIDER_CODEX_CHATGPT",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENAI_COMPATIBLE",
    "build_rule_interpretations",
    "generate_interpretations",
    "request_codex_chatgpt_interpretations",
    "request_llm_interpretations",
]
