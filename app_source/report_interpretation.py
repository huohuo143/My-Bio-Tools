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
from llm_providers import (
    CLOUD_API_PROVIDERS,
    PROVIDER_CHATANYWHERE,
    PROVIDER_DEEPSEEK,
    PROVIDER_DOUBAO,
    PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_QWEN,
    PROVIDER_ZHIPU,
    chat_completions_url,
    cloud_provider,
    cloud_provider_label,
)
from mechanism_evidence import MAX_AI_ABSTRACTS, MAX_AI_CLAIMS, build_mechanism_claims, rank_claims_for_ai
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
_RICE_IDENTIFIER = re.compile(r"(?:LOC_Os\d{2}g\d{5}(?:\.\d+)?|Os\d{2}[gt]\d{7}(?:-\d+)?)")


def _message_content(payload: dict[str, object]) -> str:
    choices = payload.get("choices") or []
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    message = choices[0].get("message") or {}
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(str(item["text"]))
        return "\n".join(parts).strip()
    return ""


def _post_cloud_chat(
    client: requests.Session,
    *,
    provider: str,
    base_url: str,
    api_key: str,
    payload: dict[str, object],
    timeout: int,
) -> requests.Response:
    """Post one compatible request and retry without JSON mode if rejected."""
    url = chat_completions_url(base_url)
    headers = {
        "Authorization": f"Bearer {api_key.strip()}",
        "Content-Type": "application/json",
    }
    response = client.post(url, headers=headers, json=payload, timeout=timeout)
    try:
        response.raise_for_status()
        return response
    except requests.HTTPError:
        status_code = int(getattr(response, "status_code", 0) or 0)
        if "response_format" not in payload or status_code not in {400, 422}:
            raise
    fallback_payload = dict(payload)
    fallback_payload.pop("response_format", None)
    response = client.post(url, headers=headers, json=fallback_payload, timeout=timeout)
    response.raise_for_status()
    return response


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
    elif provider in CLOUD_API_PROVIDERS:
        if not api_key.strip():
            raise ValueError("请先填写 API Key。")
        response = _post_cloud_chat(
            client,
            provider=provider,
            base_url=normalized,
            api_key=api_key,
            payload={
                "model": selected_model,
                "temperature": 0,
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "Reply with OK."}],
            },
            timeout=timeout,
        )
        content = _message_content(response.json())
    else:
        raise ValueError(f"不支持的大模型提供方：{provider}")
    if not content:
        raise ValueError("模型服务已响应，但没有返回内容。")
    elapsed = time.monotonic() - started
    label = cloud_provider_label(provider) if provider in CLOUD_API_PROVIDERS else "Ollama"
    return f"{label} · {selected_model} · {elapsed:.1f} 秒"


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
                "可统计水稻多组学解读",
                "当前基因未命中具有生物学重复的多组学差异或项目内定量记录，不能据此判断处理响应方向。",
                f"差异记录 0 条；项目内定量记录 {len(bundle.lab_omics_profiles)} 条。",
                "数据缺口",
                "不适用",
                "可能是数据库尚未解锁、ID 未映射，或当前基因未进入已纳入的可统计数据集。论文证据单独展示，不参与此处自动机制解读。",
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
    mechanism = len(bundle.mechanism_claims)
    literature = len(bundle.literature_rows)
    omics_available = bool(bundle.lab_omics_differential or bundle.lab_omics_profiles)
    haplotypes_available = bool(bundle.haplotypes)
    if direct:
        evidence_statement = f"已有 {direct} 条数据库/人工遗传或功能证据，可作为解释起点"
    elif mechanism:
        evidence_statement = f"已整理 {mechanism} 条可追溯的功能/机制证据，应按证据等级综合解读"
    elif literature:
        evidence_statement = f"检索到 {literature} 篇关联文献，但仍需逐篇核验全文与当前基因的直接关系"
    else:
        evidence_statement = "尚无可直接支撑功能结论的遗传证据或关联文献"
    support = []
    if omics_available:
        support.append("具有生物学重复的水稻多组学提供处理响应线索")
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
                f"遗传/功能证据 {direct} 条；机制证据 {mechanism} 条；关联文献 {literature} 篇；eFP {len(bundle.efp_rows)} 条；"
                f"可统计水稻多组学差异 {len(bundle.lab_omics_differential)} 条；单倍型 {len(bundle.haplotypes)} 个。"
            ),
            "规则化综合判断",
            "中" if (direct or mechanism) and (omics_available or haplotypes_available or literature) else "低-中",
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
    if not bundle.mechanism_claims:
        bundle.mechanism_claims = build_mechanism_claims(bundle)

    def safe(value: object) -> object:
        if isinstance(value, str):
            return _deidentify_text(value, replacements)
        if isinstance(value, dict):
            return {str(key): safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [safe(item) for item in value]
        return value

    def selected(rows: list[dict[str, object]], keys: tuple[str, ...], limit: int) -> list[dict[str, object]]:
        return [
            {key: safe(row.get(key)) for key in keys if row.get(key) not in (None, "")}
            for row in rows[:limit]
        ]

    multiomics = sorted(bundle.lab_omics_differential, key=lambda row: -abs(_as_float(row.get("log2fc")) or 0))[:40]
    safe_multiomics = selected(
        multiomics,
        (
            "msu_locus", "comparison_name", "treatment", "time_label", "assay", "feature_type",
            "log2fc", "pvalue", "padj", "regulated", "site_position", "site_residue", "descriptive", "dataset_name",
        ),
        40,
    )
    safe_haplotypes = selected(
        bundle.haplotypes,
        ("input_id", "rap_gene", "haplotype", "sample_count", "sample_frequency", "filtered_variant_count"),
        20,
    )
    safe_rule_rows = [safe({key: value for key, value in row.items() if key != "source_refs"}) for row in rule_rows]
    ranked_claims = rank_claims_for_ai(bundle.mechanism_claims, MAX_AI_CLAIMS)
    literature_with_abstracts = [row for row in bundle.literature_rows if str(row.get("abstract_text") or "").strip()]
    literature_ranked = sorted(
        literature_with_abstracts,
        key=lambda row: (
            0 if str(row.get("verification_status") or "").startswith("直接支持") else 1,
            -int(row.get("year") or 0) if str(row.get("year") or "").isdigit() else 0,
        ),
    )[:MAX_AI_ABSTRACTS]
    safe_analysis_objects = []
    for index, value in enumerate(bundle.inputs, 1):
        text = str(value or "").strip()
        if _RICE_IDENTIFIER.fullmatch(text):
            safe_analysis_objects.append(text)
        else:
            safe_analysis_objects.append(f"input_{index}")
    identity_rows = selected(
        bundle.ricedata_rows,
        (
            "check", "GeneID", "GeneName", "GeneSymbol", "RAP_Locus", "MSU_Locus", "NCBI_Locus",
            "RefSeq_Locus_Prot", "Uniprots", "亚细胞定位", "source_url",
        ),
        10,
    )
    sequence_summaries = [record.summary_row() for record in bundle.sequences]
    report_modules = {
        "word_report_sections": {
            "identity": len(bundle.mapping_rows), "known_evidence": len(bundle.genetic_evidence),
            "mechanism_claims": len(bundle.mechanism_claims), "expression": len(bundle.efp_rows),
            "sequences": len(bundle.sequences), "predictions": len(bundle.predictions),
            "regulation": len(bundle.promoter_tfbs) + len(bundle.upstream_tfs) + len(bundle.mirna_targets),
            "variants": len(bundle.variants), "literature": len(bundle.literature_rows),
        },
        "excel_sheet_records": {
            "RiceData": len(bundle.ricedata_rows), "Mechanism_Evidence": len(bundle.mechanism_claims),
            "eFP_Expression": len(bundle.efp_rows), "Lab_Omics_Differential": len(bundle.lab_omics_differential),
            "Protein_Domains": len(bundle.protein_domains), "Functional_Sites": len(bundle.functional_sites),
            "Promoter_TFBS": len(bundle.promoter_tfbs), "Variants": len(bundle.variants),
            "Haplotypes": len(bundle.haplotypes), "Literature": len(bundle.literature_rows),
        },
        "zip_manifest_information": {
            "analysis_parameters": safe({key: value for key, value in bundle.analysis_options.items() if not key.endswith("_name")}),
            "source_count": len(bundle.sources), "sources": safe(bundle.sources[:30]),
            "warning_count": len(bundle.warnings), "warnings": safe(bundle.warnings[:30]),
        },
    }
    return {
        "analysis_object": safe_analysis_objects,
        "analysis_object_count": len(bundle.inputs),
        "rule_interpretations": safe_rule_rows,
        "gene_identity_and_annotation": identity_rows,
        "mechanism_claims": safe(ranked_claims),
        "mechanism_claim_count": len(bundle.mechanism_claims),
        "mechanism_claims_sent": len(ranked_claims),
        "mechanism_claims_omitted": max(0, len(bundle.mechanism_claims) - len(ranked_claims)),
        "key_literature_with_abstracts": selected(
            literature_ranked,
            ("reference_id", "pmid", "doi", "title", "year", "journal", "authors", "abstract_text", "verification_status", "source_url"),
            MAX_AI_ABSTRACTS,
        ),
        "literature_metadata": selected(
            bundle.literature_rows,
            ("reference_id", "pmid", "doi", "title", "year", "journal", "authors", "evidence_tags", "verification_status", "source_type", "source_url"),
            40,
        ),
        "genetic_and_manual_evidence": selected(
            bundle.genetic_evidence,
            ("input_id", "rap_gene", "msu_id", "gene_symbol", "evidence_type", "evidence_text", "linked_dois", "verification_status", "source_type", "source_url"),
            40,
        ),
        "literature_abstracts_sent": len(literature_ranked),
        "report_information_coverage": report_modules,
        "multiomics_top_records": safe_multiomics,
        "multiomics_profiles": selected(
            bundle.lab_omics_profiles,
            ("msu_locus", "dataset_name", "treatment", "time_label", "assay", "feature_type", "abundance", "unit", "site_position", "site_residue"),
            40,
        ),
        "haplotype_summary": safe_haplotypes,
        "protein_domains": selected(bundle.protein_domains, ("protein_id", "database", "accession", "name", "description", "feature_type", "start", "end", "go_terms", "pathways", "status", "source_url"), 30),
        "functional_sites": selected(bundle.functional_sites, ("protein_id", "database", "accession", "site_type", "description", "start", "end", "residue", "status", "source_url"), 30),
        "localization_predictions": safe([result.summary_row() for result in bundle.predictions[:20]]),
        "expression_records": safe([record.summary_row() for record in bundle.efp_rows[:40]]),
        "sequence_and_model_summaries": safe(sequence_summaries[:30]),
        "transcript_models": selected(bundle.transcript_models, tuple(bundle.transcript_models[0].keys()) if bundle.transcript_models else (), 20),
        "gene_features": selected(bundle.gene_features, tuple(bundle.gene_features[0].keys()) if bundle.gene_features else (), 30),
        "promoter_tfbs": selected(bundle.promoter_tfbs, tuple(bundle.promoter_tfbs[0].keys()) if bundle.promoter_tfbs else (), 30),
        "upstream_tfs": selected(bundle.upstream_tfs, tuple(bundle.upstream_tfs[0].keys()) if bundle.upstream_tfs else (), 20),
        "variants": selected(bundle.variants, tuple(bundle.variants[0].keys()) if bundle.variants else (), 30),
        "mirna_targets": selected(bundle.mirna_targets, tuple(bundle.mirna_targets[0].keys()) if bundle.mirna_targets else (), 20),
        "rnai_offtargets": selected(bundle.rnai_offtargets, tuple(bundle.rnai_offtargets[0].keys()) if bundle.rnai_offtargets else (), 20),
        "payload_module_coverage": {
            "mechanism_claims": {"total": len(bundle.mechanism_claims), "sent": len(ranked_claims)},
            "literature": {"total": len(bundle.literature_rows), "metadata_sent": min(len(bundle.literature_rows), 40), "abstracts_sent": len(literature_ranked)},
            "genetic_evidence": {"total": len(bundle.genetic_evidence), "sent": min(len(bundle.genetic_evidence), 40)},
            "efp": {"total": len(bundle.efp_rows), "sent": min(len(bundle.efp_rows), 40)},
            "lab_omics_differential": {"total": len(bundle.lab_omics_differential), "sent": len(safe_multiomics)},
            "lab_omics_profiles": {"total": len(bundle.lab_omics_profiles), "sent": min(len(bundle.lab_omics_profiles), 40)},
            "protein_domains": {"total": len(bundle.protein_domains), "sent": min(len(bundle.protein_domains), 30)},
            "promoter_tfbs": {"total": len(bundle.promoter_tfbs), "sent": min(len(bundle.promoter_tfbs), 30)},
            "variants": {"total": len(bundle.variants), "sent": min(len(bundle.variants), 30)},
        },
        "evidence_counts": {
            "genetic_evidence": len(bundle.genetic_evidence),
            "mechanism_claims": len(bundle.mechanism_claims),
            "literature": len(bundle.literature_rows),
            "efp": len(bundle.efp_rows),
            "variants": len(bundle.variants),
        },
        "privacy_note": (
            "The payload contains the interpretable information represented in the Word, Excel and ZIP deliverables, "
            "but not their binary bytes, raw sequences, images, individual sample names, secrets or local file paths."
        ),
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


def _require_deep_payload(payload: dict[str, object]) -> None:
    for key in ("gene_identity", "core_function"):
        if not isinstance(payload.get(key), dict):
            raise ValueError(f"大模型未返回深度解读字段：{key}。")
    for key in ("mechanism_chains", "context_branches", "omics_integration", "testable_hypotheses", "knowledge_gaps", "references"):
        if not isinstance(payload.get(key), list):
            raise ValueError(f"大模型未返回深度解读字段：{key}。")


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
                    "ai_integrated",
                    f"AI候选假设 {index}",
                    hypothesis,
                    str(item.get("support") or "模型未明确列出支持证据。"),
                    "AI辅助推断", "待人工核验",
                    str(item.get("limitations") or "尚未形成因果证据链。"),
                    str(item.get("experiment") or "需要设计区分性实验验证。"),
                )
            )
    return output


def _valid_evidence_ids(value: object, valid: set[str]) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item) for item in value if str(item) in valid))


def build_evidence_synthesis(bundle: "AnalysisBundle") -> dict[str, object]:
    """Create a useful standalone report even when the model is unavailable."""
    if not bundle.mechanism_claims:
        bundle.mechanism_claims = build_mechanism_claims(bundle)
    claims = rank_claims_for_ai(bundle.mechanism_claims, MAX_AI_CLAIMS)
    identity_claims = [row for row in claims if row.get("category") == "gene_identity"]
    mechanism_claims = [row for row in claims if row.get("category") not in {"gene_identity", "project_omics", "expression_context"}]
    omics_claims = [row for row in claims if row.get("category") in {"project_omics", "expression_context"}]
    identity_text = str(identity_claims[0].get("statement") or "") if identity_claims else "当前数据库未返回可用的基因名称或分子身份。"
    core_text = str(mechanism_claims[0].get("statement") or "") if mechanism_claims else "当前没有足以形成功能结论的机制证据。"
    chains = []
    for row in mechanism_claims[:5]:
        chains.append({
            "title": str(row.get("context") or row.get("category") or "机制证据"),
            "context": str(row.get("context") or "未特异场景"),
            "upstream": "请从原始证据陈述中核验",
            "molecular_event": str(row.get("statement") or ""),
            "downstream": str(row.get("related_entities") or "待进一步结构化"),
            "phenotype": "见证据原文",
            "evidence_ids": [str(row.get("evidence_id"))],
            "confidence": str(row.get("evidence_level") or "数据库整理"),
        })
    return {
        "report_mode": "evidence_fallback",
        "executive_summary": core_text,
        "gene_identity": {
            "summary": identity_text, "molecular_role": "见数据库注释与结构域证据",
            "localization": "见定位证据与计算预测", "evidence_ids": [str(row.get("evidence_id")) for row in identity_claims[:3]],
        },
        "core_function": {"summary": core_text, "evidence_ids": [str(row.get("evidence_id")) for row in mechanism_claims[:3]]},
        "mechanism_chains": chains,
        "context_branches": [
            {"context": str(row.get("context") or "未特异场景"), "interpretation": str(row.get("statement") or ""), "evidence_ids": [str(row.get("evidence_id"))]}
            for row in mechanism_claims[:5]
        ],
        "omics_integration": [
            {"observation": str(row.get("statement") or ""), "interpretation": "当前仅作为组学观察，不自动升格为因果机制。", "status": "本次观察", "evidence_ids": [str(row.get("evidence_id"))]}
            for row in omics_claims[:8]
        ],
        "testable_hypotheses": [],
        "knowledge_gaps": ["未完成 AI 综合；应逐条核验原始证据、实验背景和全文。"],
        "references": [str(row.get("evidence_id")) for row in claims],
    }


def normalize_ai_synthesis(payload: dict[str, object], bundle: "AnalysisBundle") -> dict[str, object]:
    """Keep only model content that points to evidence present in this run."""
    fallback = build_evidence_synthesis(bundle)
    valid = {str(row.get("evidence_id")) for row in bundle.mechanism_claims if row.get("evidence_id")}
    identity = payload.get("gene_identity") if isinstance(payload.get("gene_identity"), dict) else {}
    core = payload.get("core_function") if isinstance(payload.get("core_function"), dict) else {}

    def grounded_items(key: str) -> list[dict[str, object]]:
        items = payload.get(key)
        output: list[dict[str, object]] = []
        if not isinstance(items, list):
            return output
        for item in items:
            if not isinstance(item, dict):
                continue
            ids = _valid_evidence_ids(item.get("evidence_ids"), valid)
            if not ids:
                continue
            output.append({**item, "evidence_ids": ids})
        return output

    identity_ids = _valid_evidence_ids(identity.get("evidence_ids"), valid)
    core_ids = _valid_evidence_ids(core.get("evidence_ids"), valid)
    synthesis = {
        "report_mode": "ai",
        "executive_summary": str(payload.get("executive_summary") or core.get("summary") or fallback["executive_summary"]),
        "gene_identity": {
            "summary": str(identity.get("summary") or fallback["gene_identity"]["summary"]),
            "molecular_role": str(identity.get("molecular_role") or fallback["gene_identity"]["molecular_role"]),
            "localization": str(identity.get("localization") or fallback["gene_identity"]["localization"]),
            "evidence_ids": identity_ids or fallback["gene_identity"]["evidence_ids"],
        },
        "core_function": {
            "summary": str(core.get("summary") or fallback["core_function"]["summary"]),
            "evidence_ids": core_ids or fallback["core_function"]["evidence_ids"],
        },
        "mechanism_chains": grounded_items("mechanism_chains") or fallback["mechanism_chains"],
        "context_branches": grounded_items("context_branches") or fallback["context_branches"],
        "omics_integration": grounded_items("omics_integration") or fallback["omics_integration"],
        "testable_hypotheses": grounded_items("testable_hypotheses"),
        "knowledge_gaps": [str(item) for item in payload.get("knowledge_gaps", []) if str(item).strip()][:8]
            if isinstance(payload.get("knowledge_gaps"), list) else fallback["knowledge_gaps"],
        "references": _valid_evidence_ids(payload.get("references"), valid) or fallback["references"],
    }
    return synthesis


def _llm_prompt(bundle: "AnalysisBundle", rule_rows: list[dict[str, object]]) -> tuple[str, str]:
    evidence = json.dumps(_llm_payload(bundle, rule_rows), ensure_ascii=False, separators=(",", ":"))
    system = (
        "你是植物分子生物学科研报告助手。只能使用用户提供的结构化证据，不得补写数据库外事实、"
        "不得把相关性或计算预测写成因果。请区分已有证据、组学支持、合理推测和需实验验证。"
        "你将同时看到 Word 正文、Excel 工作表与 ZIP 清单所代表的同源结构化信息。"
        "重点解释基因的分子身份、主要功能、上游—分子事件—下游—表型机制链、场景分支和可区分的实验。"
        "必须区分已知机制、本次组学观察与新假设；PTM位点不得与总蛋白混同。"
        "每个事实性结论必须引用 mechanism_claims 中存在的 evidence_id。"
        "不得调用工具、读取本机文件、联网搜索或引用结构化证据以外的内容。"
        "返回严格JSON，字段为 executive_summary、multiomics_interpretation、haplotype_interpretation、"
        "integrated_hypotheses，并完整返回 gene_identity、core_function、mechanism_chains、context_branches、"
        "omics_integration、testable_hypotheses、knowledge_gaps、references。"
        "mechanism_chains 限 3–5 条，引用只能使用输入中的 evidence_id。"
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
) -> tuple[list[dict[str, object]], dict[str, object]]:
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
    elif provider in CLOUD_API_PROVIDERS:
        if not api_key.strip():
            raise ValueError("云端大模型需要 API Key。")
        preset = cloud_provider(provider)
        request_payload: dict[str, object] = {
            "model": model.strip(),
            "temperature": 0.1,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        }
        if preset.json_object_mode:
            request_payload["response_format"] = {"type": "json_object"}
        response = _post_cloud_chat(
            client,
            provider=provider,
            base_url=normalized,
            api_key=api_key,
            payload=request_payload,
            timeout=timeout,
        )
        content = _message_content(response.json())
    else:
        raise ValueError(f"不支持的大模型提供方：{provider}")
    if not content.strip():
        raise ValueError("大模型返回了空内容。")
    parsed = _extract_json(content)
    _require_deep_payload(parsed)
    rows = _llm_rows(parsed)
    if not rows:
        raise ValueError("大模型未返回可用的解读字段。")
    return rows, parsed


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
    if not bundle.mechanism_claims:
        bundle.mechanism_claims = build_mechanism_claims(bundle)
    rules = build_rule_interpretations(bundle)
    bundle.ai_synthesis = build_evidence_synthesis(bundle)
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
        "synthesis_version": "2.0",
        "evidence_claim_count": len(bundle.mechanism_claims),
        "evidence_claims_sent": min(len(bundle.mechanism_claims), MAX_AI_CLAIMS),
        "evidence_claims_omitted": max(0, len(bundle.mechanism_claims) - MAX_AI_CLAIMS),
        "literature_abstracts_sent": min(
            sum(bool(str(row.get("abstract_text") or "").strip()) for row in bundle.literature_rows),
            MAX_AI_ABSTRACTS,
        ),
        "ai_report_mode": "not_requested" if mode != MODE_LLM else "evidence_fallback",
        "ai_report_status": "not_requested" if mode != MODE_LLM else "pending",
        "evidence_reference_validation": "not_run",
        "error_code": "",
        "error": "",
        "privacy": (
            "离线规则解读未向外部模型发送数据。"
            if mode != MODE_LLM
            else "模型接收 Word、Excel 与 ZIP 中可解释内容的同源结构化证据；不发送二进制文件、原始序列、图片、样本名、密码、令牌、密钥或本地路径。"
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
            ai_payload = result.payload
        else:
            ai_rows, ai_payload = request_llm_interpretations(
                bundle,
                rules,
                provider=provider,
                base_url=base_url,
                model=model,
                api_key=api_key,
                session=session,
            )
            status["provider_label"] = "本机 Ollama" if provider == PROVIDER_OLLAMA else cloud_provider_label(provider)
        status["effective_mode"] = MODE_LLM
        status["ai_section_count"] = len(ai_rows)
        bundle.ai_synthesis = normalize_ai_synthesis(ai_payload, bundle)
        status["ai_report_mode"] = "ai"
        status["ai_report_status"] = "ready"
        status["evidence_reference_validation"] = "validated"
        return [*rules, *ai_rows], status
    except codex_chatgpt.CodexInvocationCancelled:
        raise
    except codex_chatgpt.CodexClientError as exc:
        status["client_version"] = exc.client_version
        status["error_code"] = exc.code
        status["error"] = exc.user_message
        status["ai_report_status"] = "evidence_fallback_ready"
        return rules, status
    except Exception as exc:
        status["error_code"] = "provider_error"
        status["error"] = f"{type(exc).__name__}: {exc}"
        status["ai_report_status"] = "evidence_fallback_ready"
        return rules, status


__all__ = [
    "MODE_LLM",
    "MODE_RULES",
    "PROVIDER_CODEX_CHATGPT",
    "PROVIDER_CHATANYWHERE",
    "PROVIDER_DEEPSEEK",
    "PROVIDER_DOUBAO",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENAI_COMPATIBLE",
    "PROVIDER_QWEN",
    "PROVIDER_ZHIPU",
    "build_rule_interpretations",
    "generate_interpretations",
    "request_codex_chatgpt_interpretations",
    "request_llm_interpretations",
]
