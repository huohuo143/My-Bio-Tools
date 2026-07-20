"""Normalize report-wide evidence into traceable mechanism claims."""

from __future__ import annotations

from collections import defaultdict
import re
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from rice_gene_core import AnalysisBundle


MAX_AI_CLAIMS = 40
MAX_AI_ABSTRACTS = 12

_CITATION = re.compile(
    r"(?P<author>[A-Z][A-Za-z'\-]+)\s+et\s+al\.?\s*,?\s*(?P<year>(?:19|20)\d{2})",
    re.I,
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_GENE_TOKEN = re.compile(
    r"\b(?:Os[A-Za-z0-9-]+|IPA1|D53|DEP1|SNAC1|GS2|PAL1|IPI7|"
    r"miR\d+[A-Za-z-]*|NRT1\.1B|PILS\d+[A-Za-z-]*|PCF\d+)\b",
    re.I,
)
_METHOD_TERMS = (
    "CRISPR", "突变体", "敲除", "过表达", "互补", "Y2H", "BiFC", "Co-IP",
    "CoIP", "pull-down", "EMSA", "ChIP", "dual-LUC", "LUC", "Y1H", "定位",
    "磷酸化", "泛素化", "降解", "互作", "结合", "激活", "抑制",
)
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("post_translational_regulation", ("磷酸化", "泛素化", "降解", "稳定")),
    ("protein_interaction", ("互作", "Y2H", "BiFC", "Co-IP", "pull-down")),
    ("transcriptional_regulation", ("启动子", "结合", "转录", "激活", "抑制", "GTAC")),
    ("genetic_phenotype", ("突变体", "敲除", "过表达", "表型", "QTL", "等位基因")),
    ("expression_localization", ("表达", "定位", "细胞核", "组织")),
    ("biological_function", ("调控", "耐", "抗", "产量", "发育", "休眠")),
)
_CONTEXT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("免疫/病原响应", ("稻瘟", "病毒", "白叶枯", "病原", "免疫", "抗病")),
    ("非生物胁迫", ("盐胁迫", "冷胁迫", "低温", "干旱", "镉", "氧化胁迫")),
    ("株型与产量", ("分蘖", "穗", "产量", "株高", "粒重", "枝梗")),
    ("根系与营养", ("根", "硝态氮", "氮", "糖分配")),
    ("种子与品质", ("休眠", "穗发芽", "籽粒", "淀粉", "垩白")),
)


def _compact(value: object, limit: int = 2200) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _study_chunks(value: object) -> list[str]:
    """Group RiceData prose into citation-bearing study-sized chunks."""
    text = _compact(value, 100_000)
    if not text:
        return []
    text = text.split("【相关登录号】", 1)[0]
    text = re.sub(r"【(?:亚细胞定位|生物学功能|时空表达谱|定位与克隆|突变体表型)】", " ", text)
    sentences = [item.strip() for item in re.split(r"(?<=[。！？])\s+", text) if item.strip()]
    chunks: list[str] = []
    pending: list[str] = []
    for sentence in sentences:
        pending.append(sentence)
        joined = " ".join(pending)
        if _CITATION.search(sentence) or len(joined) >= 1200:
            chunks.append(_compact(joined))
            pending = []
    if pending:
        chunks.append(_compact(" ".join(pending)))
    return [chunk for chunk in chunks if len(chunk) >= 20]


def _classify(text: str) -> str:
    for category, terms in _CATEGORY_RULES:
        if any(term.casefold() in text.casefold() for term in terms):
            return category
    return "biological_function"


def _context(text: str) -> str:
    labels = [label for label, terms in _CONTEXT_RULES if any(term.casefold() in text.casefold() for term in terms)]
    return "、".join(labels[:3]) or "未特异场景"


def _methods(text: str) -> str:
    return "、".join(term for term in _METHOD_TERMS if term.casefold() in text.casefold())


def _matching_references(
    text: str,
    references: Iterable[dict[str, object]],
) -> tuple[list[dict[str, object]], str]:
    citations = [(match.group("author").casefold(), match.group("year")) for match in _CITATION.finditer(text)]
    years = set(_YEAR.findall(text))
    exact: list[dict[str, object]] = []
    year_only: list[dict[str, object]] = []
    for reference in references:
        year = str(reference.get("year") or "")
        if year not in years:
            continue
        authors = str(reference.get("authors") or "").casefold()
        if any(cited_year == year and cited_author in authors for cited_author, cited_year in citations if authors):
            exact.append(reference)
        else:
            year_only.append(reference)
    if exact:
        return exact, "citation_author_year"
    return year_only, "citation_year" if year_only else "database_prose"


def _claim(
    *,
    statement: str,
    category: str,
    context: str,
    gene: str,
    aliases: str,
    source_type: str,
    source_url: str,
    evidence_level: str,
    references: Iterable[dict[str, object]] = (),
    matched_by: str = "",
    input_id: str = "",
) -> dict[str, object]:
    linked = list(references)
    return {
        "evidence_id": "",
        "input_id": input_id,
        "gene_id": gene,
        "aliases": aliases,
        "category": category,
        "statement": _compact(statement),
        "context": context,
        "related_entities": "、".join(dict.fromkeys(match.group(0) for match in _GENE_TOKEN.finditer(statement))),
        "experimental_methods": _methods(statement),
        "reference_ids": ",".join(str(row.get("reference_id") or "") for row in linked if row.get("reference_id")),
        "dois": ",".join(str(row.get("doi") or "") for row in linked if row.get("doi")),
        "source_type": source_type,
        "source_url": source_url,
        "evidence_level": evidence_level,
        "verification_status": (
            "作者与年份匹配" if matched_by == "citation_author_year"
            else "年份匹配，需全文核验" if matched_by == "citation_year"
            else "数据库整理陈述"
        ),
        "matched_by": matched_by,
    }


def mechanism_claims_from_ricedata(
    rows: list[dict[str, object]],
    references: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    references_by_gene: dict[str, list[dict[str, object]]] = defaultdict(list)
    for reference in references or []:
        references_by_gene[str(reference.get("gene_id") or "")].append(reference)
    claims: list[dict[str, object]] = []
    for row in rows:
        gene = str(row.get("RAP_Locus") or row.get("MSU_Locus") or row.get("check") or "")
        aliases = "; ".join(value for value in (str(row.get("GeneSymbol") or ""), str(row.get("GeneName") or "")) if value)
        gene_refs = references_by_gene.get(str(row.get("GeneID") or ""), [])
        identity = "; ".join(value for value in (str(row.get("GeneName") or ""), str(row.get("GeneSymbol") or "")) if value)
        if identity:
            claims.append(_claim(
                statement=f"数据库基因身份：{identity}。", category="gene_identity", context="基因身份",
                gene=gene, aliases=aliases, source_type="RiceData annotation", source_url=str(row.get("source_url") or ""),
                evidence_level="数据库整理", input_id=str(row.get("check") or ""),
            ))
        for key, category in (("突变体表型", "genetic_phenotype"), ("定位与克隆", "genetic_phenotype"), ("时空表达谱", "expression_localization"), ("亚细胞定位", "expression_localization"), ("生物学功能", "biological_function")):
            statement = _compact(row.get(key))
            if not statement:
                continue
            linked, matched_by = _matching_references(statement, gene_refs)
            claims.append(_claim(
                statement=statement, category=category, context=_context(statement), gene=gene, aliases=aliases,
                source_type=f"RiceData {key}", source_url=str(row.get("source_url") or ""),
                evidence_level="论文支持" if linked else "数据库整理", references=linked,
                matched_by=matched_by, input_id=str(row.get("check") or ""),
            ))
        for statement in _study_chunks(row.get("其他信息")):
            linked, matched_by = _matching_references(statement, gene_refs)
            methods = _methods(statement)
            level = "直接遗传/分子证据" if linked and methods else "论文支持" if linked else "数据库整理"
            claims.append(_claim(
                statement=statement, category=_classify(statement), context=_context(statement), gene=gene, aliases=aliases,
                source_type="RiceData mechanism narrative", source_url=str(row.get("source_url") or ""),
                evidence_level=level, references=linked, matched_by=matched_by,
                input_id=str(row.get("check") or ""),
            ))
    return _dedupe_and_number(claims)


def _dedupe_and_number(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (str(row.get("gene_id") or "").casefold(), str(row.get("statement") or "").casefold())
        if not key[1] or key in seen:
            continue
        seen.add(key)
        copied = dict(row)
        copied["evidence_id"] = f"E{len(output) + 1:03d}"
        output.append(copied)
    return output


def build_mechanism_claims(bundle: "AnalysisBundle") -> list[dict[str, object]]:
    claims = mechanism_claims_from_ricedata(bundle.ricedata_rows, bundle.ricedata_references)
    gene = next((str(row.get("resolved_rap_gene") or row.get("resolved_msu_id") or "") for row in bundle.mapping_rows), "")
    extras: list[dict[str, object]] = []
    for row in bundle.protein_domains:
        if row.get("status") != "matched":
            continue
        statement = "; ".join(value for value in (
            str(row.get("name") or row.get("accession") or ""), str(row.get("description") or ""),
            f"GO: {row.get('go_terms')}" if row.get("go_terms") else "",
            f"Pathway: {row.get('pathways')}" if row.get("pathways") else "",
        ) if value)
        extras.append(_claim(statement=statement, category="protein_domain", context="分子结构", gene=gene,
            aliases="", source_type="InterPro", source_url=str(row.get("source_url") or ""), evidence_level="计算预测"))
    for result in bundle.predictions:
        if result.status not in {"matched", "partial"}:
            continue
        statement = f"{result.tool}: {result.classification or result.summary}"
        extras.append(_claim(statement=statement, category="localization_prediction", context="亚细胞定位", gene=gene,
            aliases="", source_type=result.tool, source_url=result.result_url, evidence_level="计算预测"))
    for row in bundle.lab_omics_differential:
        assay = str(row.get("assay") or row.get("feature_type") or "未标注组学")
        context = "; ".join(value for value in (str(row.get("comparison_name") or row.get("treatment") or ""), str(row.get("time_label") or "")) if value)
        statement = f"{context or '未标注处理'}：{assay} log2FC={row.get('log2fc')}"
        if row.get("site_position") is not None:
            statement += f"，位点 {row.get('site_residue') or ''}{row.get('site_position')}"
        extras.append(_claim(statement=statement, category="project_omics", context=context or "实验室项目", gene=gene,
            aliases="", source_type="lab analysed omics", source_url="", evidence_level="组学相关"))
    for record in bundle.efp_rows:
        if record.status != "matched" or record.expression_level is None:
            continue
        statement = f"{record.data_source_label or record.data_source} / {record.tissue or record.group}: Absolute={record.expression_level}"
        extras.append(_claim(statement=statement, category="expression_context", context=record.tissue or record.group or "eFP", gene=gene,
            aliases="", source_type="Rice eFP", source_url=record.experiment_url, evidence_level="组学相关"))
    return _dedupe_and_number([*claims, *extras])


def rank_claims_for_ai(claims: list[dict[str, object]], limit: int = MAX_AI_CLAIMS) -> list[dict[str, object]]:
    priority = {
        "直接遗传/分子证据": 0, "论文支持": 1, "数据库整理": 2,
        "组学相关": 3, "计算预测": 4, "待验证假设": 5,
    }
    return sorted(
        claims,
        key=lambda row: (
            priority.get(str(row.get("evidence_level") or ""), 9),
            0 if row.get("experimental_methods") else 1,
            0 if row.get("dois") else 1,
            str(row.get("evidence_id") or ""),
        ),
    )[:limit]


__all__ = [
    "MAX_AI_ABSTRACTS", "MAX_AI_CLAIMS", "build_mechanism_claims",
    "mechanism_claims_from_ricedata", "rank_claims_for_ai",
]
