"""Retrieve quantitative rice eFP values and generate report-ready figures."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
import copy
import io
import re
import threading
import time
from typing import Callable, Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from plot_style import CVD_PALETTE, INK, LIGHT, MUTED, OTHER, add_axis_title, publication_context, style_axis


EFP_URL = "https://bar.utoronto.ca/transcriptomics/efp_rice/cgi-bin/efpWeb.cgi"
EFP_OUTPUT_ROOT = "https://bar.utoronto.ca/transcriptomics/efp_rice/output/"
EFP_GUIDE_URL = "https://bar.utoronto.ca/affydb/BAR_instructions.html#efp"
EFP_MODE = "Absolute"
EFP_CACHE_SECONDS = 900
EFP_MAX_GENES = 20

EFP_DATA_SOURCES: dict[str, str] = {
    "rice_rma": "Developmental atlas (RMA)",
    "ricestress_rma": "Stress atlas (RMA)",
    "rice_mas": "Developmental atlas (MAS)",
    "ricestress_mas": "Stress atlas (MAS)",
    "rice_drought_heat_stress": "Drought and heat stress",
    "rice_leaf_gradient": "Leaf gradient",
    "rice_maize_comparison": "Rice-maize comparison",
    "rice_single_cell": "Single-cell atlas",
    "riceanoxia_rma": "Anoxia (RMA)",
    "riceanoxia_mas": "Anoxia (MAS)",
    "ricestigma_rma": "Stigma (RMA)",
    "ricestigma_mas": "Stigma (MAS)",
}
EFP_SOURCE_GLOSSARY: dict[str, dict[str, str]] = {
    "rice_rma": {
        "name_zh": "发育组织图谱（RMA）",
        "scope": "根、叶、SAM、花序 P1/P2-P6、种子 S1-S5",
        "design": "发育组织图谱；同一组织集合的 RMA 归一化版本",
        "scale": "RMA，通常为 log2 汇总值",
        "id_namespace": "MSU",
        "reference": "BAR 官方配置；GEO GSE6893",
        "replicate_note": "15 个组织/阶段；BAR 配置中每项列出 3 个样本",
        "best_for": "判断组织特异性、营养生长与生殖发育阶段的相对表达模式",
        "outputs": "各组织平均表达值、SD、样本/实验链接与 probe ID",
        "caution": "适合同一数据集内比较；不能与 MAS5、FPKM 或 normalized counts 直接比数值大小",
    },
    "ricestress_rma": {
        "name_zh": "非生物胁迫图谱（RMA）",
        "scope": "对照、干旱、盐和冷处理下的地上部/根",
        "design": "胁迫处理图谱；同一处理集合的 RMA 归一化版本",
        "scale": "RMA，通常为 log2 汇总值",
        "id_namespace": "MSU",
        "reference": "BAR 官方配置；GEO GSE6901",
        "replicate_note": "对照/干旱/盐/冷 × shoot/root，共 8 项；每项列出 3 个样本",
        "best_for": "筛查干旱、盐、低温响应及地上部/根的差异模式",
        "outputs": "各组织×处理的平均表达值、SD、样本/实验链接与 probe ID",
        "caution": "Absolute 值不是 fold change；需要在匹配组织与对照之间解释响应",
    },
    "rice_mas": {
        "name_zh": "发育组织图谱（MAS5）",
        "scope": "根、叶、SAM、花序 P1/P2-P6、种子 S1-S5",
        "design": "发育组织图谱；与 rice_rma 相同类型组织的 MAS5 处理版本",
        "scale": "MAS5 intensity",
        "id_namespace": "MSU",
        "reference": "BAR 官方配置；GEO GSE6893",
        "replicate_note": "15 个组织/阶段；BAR 配置中每项列出 3 个样本",
        "best_for": "查看 MAS5 intensity 下的发育组织表达，并与 RMA 结果做模式复核",
        "outputs": "各组织 intensity、SD、样本/实验链接与 probe ID",
        "caution": "MAS5 与 RMA 的数值尺度不同，不应把两者的绝对值直接相减或求倍数",
    },
    "ricestress_mas": {
        "name_zh": "非生物胁迫图谱（MAS5）",
        "scope": "对照、干旱、盐和冷处理下的地上部/根",
        "design": "胁迫处理图谱；与 ricestress_rma 相同类型处理的 MAS5 版本",
        "scale": "MAS5 intensity",
        "id_namespace": "MSU",
        "reference": "BAR 官方配置；GEO GSE6901",
        "replicate_note": "对照/干旱/盐/冷 × shoot/root，共 8 项；每项列出 3 个样本",
        "best_for": "在 MAS5 尺度下检查胁迫响应方向，并与 RMA 模式交叉核对",
        "outputs": "各组织×处理 intensity、SD、样本/实验链接与 probe ID",
        "caution": "Absolute 值不是对照归一化后的 fold change；跨算法仅比较趋势，不比较数值",
    },
    "rice_drought_heat_stress": {
        "name_zh": "叶片干旱 × 热胁迫图谱",
        "scope": "充分供水/生长受限干旱与 30°C/40°C 30 min 条件下的叶片 S2-S6 区段",
        "design": "叶片空间区段×水分/温度处理组合",
        "scale": "官网 Expression Level；BAR 定量表未标注具体单位或是否经过变换",
        "id_namespace": "MSU",
        "reference": "BAR 官方配置 rice_drought_heat_stress；实验链接当前仅指向 BAR 首页",
        "replicate_note": "S2-S6 多数组合列出 4 个样本；官方表中 W40 S2 样本出现无 S2/有 S2 两种标签",
        "best_for": "分析叶片发育区段中的干旱、热及联合环境响应",
        "outputs": "各叶片区段×处理的官网 Expression Level、SD 字段和样本标签",
        "caution": "需同时考虑区段与处理；官网未标明单位，不能擅自称为 FPKM，也不可与芯片 RMA/MAS5 直接比较",
    },
    "rice_leaf_gradient": {
        "name_zh": "水稻叶片发育梯度",
        "scope": "水稻叶片发育梯度 R1-R11",
        "design": "沿叶片空间/发育方向连续取样",
        "scale": "官网 Expression Level；BAR 定量表未标注具体单位",
        "id_namespace": "MSU",
        "reference": "BAR 官方配置；Wang et al., Nat Biotechnol 2014, DOI 10.1038/nbt.3019",
        "replicate_note": "R1-R11 各为一个汇总样本；SD 字段为 0；官方配置重复列出 R7",
        "best_for": "判断基因在叶片基部到成熟区段的空间表达梯度",
        "outputs": "R1-R11 各区段官网 Expression Level、SD 字段和样本标签",
        "caution": "区段代表空间与发育的共同变化；SD=0 不代表无变异。APP 保留官网原始行，图形/Top 汇总去除完全重复记录",
    },
    "rice_maize_comparison": {
        "name_zh": "水稻—玉米叶片梯度对照",
        "scope": "水稻 R1-R11 与玉米 M1-M15 叶片梯度",
        "design": "水稻与玉米叶片空间梯度的并列数据集",
        "scale": "官网 Expression Level；BAR 定量表未标注具体单位",
        "id_namespace": "MSU",
        "reference": "BAR 官方配置；Wang et al., Nat Biotechnol 2014, DOI 10.1038/nbt.3019",
        "replicate_note": "水稻 R1-R11、玉米 M1-M15；eFP 表中每个区段为一个汇总样本，SD 字段为 0",
        "best_for": "观察水稻基因在叶片梯度中的模式，并作为跨物种趋势比较线索",
        "outputs": "水稻/玉米叶片区段官网 Expression Level、SD 字段和样本标签",
        "caution": "跨物种同源关系、区段对应和量化尺度均需单独核验，不能只按绝对值下结论",
    },
    "rice_single_cell": {
        "name_zh": "单细胞/单核叶片胁迫图谱",
        "scope": "WW、轻度/中度干旱和盐处理的细胞类型 pseudobulk",
        "design": "按处理与细胞类型汇总的单细胞表达图谱",
        "scale": "官网 pseudobulk Expression Level；BAR 定量表未标注具体计量单位",
        "id_namespace": "RAP",
        "reference": "BAR 官方配置；Robertson et al., New Phytologist 2026, DOI 10.1111/nph.71378",
        "replicate_note": "10 类细胞 × 5 种水分/盐处理，共 50 个 pseudobulk 值；每项一个汇总样本，SD 字段为 0",
        "best_for": "定位候选基因主要出现在哪些细胞类型，并筛查干旱/盐处理下的细胞类型响应",
        "outputs": "细胞类型×处理的 pseudobulk Expression Level、SD 字段和样本标签",
        "caution": "该源提交 RAP gene ID；pseudobulk 不是单细胞逐点值，SD=0 不代表无细胞间/生物学变异，也不能与 bulk 数据数值直接比较",
    },
    "riceanoxia_rma": {
        "name_zh": "胚芽鞘缺氧图谱（RMA）",
        "scope": "有氧/缺氧胚芽鞘",
        "design": "胚芽鞘氧状态对比的 RMA 版本",
        "scale": "RMA，通常为 log2 汇总值",
        "id_namespace": "MSU",
        "reference": "BAR 官方配置；GEO GSE6908",
        "replicate_note": "有氧/无氧各 2 个样本",
        "best_for": "筛查缺氧/无氧条件下的胚芽鞘响应",
        "outputs": "有氧与缺氧样本的平均表达值、SD、样本/实验链接与 probe ID",
        "caution": "组织和处理范围较窄；只能支持该实验场景下的表达线索",
    },
    "riceanoxia_mas": {
        "name_zh": "胚芽鞘缺氧图谱（MAS5）",
        "scope": "有氧/缺氧胚芽鞘",
        "design": "胚芽鞘氧状态对比的 MAS5 版本",
        "scale": "MAS5 intensity",
        "id_namespace": "MSU",
        "reference": "BAR 官方配置；GEO GSE6908",
        "replicate_note": "有氧/无氧各 2 个样本",
        "best_for": "在 MAS5 尺度下复核缺氧/无氧响应模式",
        "outputs": "有氧与缺氧样本 intensity、SD、样本/实验链接与 probe ID",
        "caution": "与 RMA 版本仅比较方向和稳健性，不直接比较数值",
    },
    "ricestigma_rma": {
        "name_zh": "柱头与雌性组织图谱（RMA）",
        "scope": "柱头、子房、悬浮细胞及多个营养/生殖组织",
        "design": "偏重柱头/雌性生殖组织并带参照组织的 RMA 图谱",
        "scale": "RMA，通常为 log2 汇总值",
        "id_namespace": "MSU",
        "reference": "BAR 官方配置；GEO GSE7951",
        "replicate_note": "柱头/子房各 3 个样本；其余参照组织各 1 个样本，重复结构不均衡",
        "best_for": "筛查柱头、子房和雌性生殖组织偏好表达",
        "outputs": "各组织平均表达值、SD、样本/实验链接与 probe ID",
        "caution": "组织来源和重复数不完全等价；单样本参照组织的 SD 不具备重复变异含义，柱头特异性需独立证据验证",
    },
    "ricestigma_mas": {
        "name_zh": "柱头与雌性组织图谱（MAS5）",
        "scope": "柱头、子房、悬浮细胞及多个营养/生殖组织",
        "design": "偏重柱头/雌性生殖组织并带参照组织的 MAS5 图谱",
        "scale": "MAS5 intensity",
        "id_namespace": "MSU",
        "reference": "BAR 官方配置；GEO GSE7951",
        "replicate_note": "柱头/子房各 3 个样本；其余参照组织各 1 个样本，重复结构不均衡",
        "best_for": "在 MAS5 尺度下复核柱头/子房富集模式",
        "outputs": "各组织 intensity、SD、样本/实验链接与 probe ID",
        "caution": "重复数不均衡；与 RMA 版本仅比较模式，绝对 intensity 不代表跨数据集可比的表达量",
    },
}
DEFAULT_EFP_DATA_SOURCES = ("rice_rma", "ricestress_rma")
MSU_GENE_PATTERN = re.compile(r"^LOC_Os\d{2}g\d{5}$", re.IGNORECASE)
RAP_GENE_PATTERN = re.compile(r"^Os\d{2}g\d{7}$", re.IGNORECASE)
_TABLE_LINK_PATTERN = re.compile(r'href=["\']\.\./output/([^"\']+\.html)["\']', re.IGNORECASE)
_PROBE_PATTERN = re.compile(r"([A-Za-z0-9_.-]+)\s+was used as the probe set identifier", re.IGNORECASE)


def efp_source_display_label(data_source: str) -> str:
    """Return a bilingual user-facing label while preserving the canonical source key."""
    english = EFP_DATA_SOURCES.get(data_source, data_source)
    chinese = EFP_SOURCE_GLOSSARY.get(data_source, {}).get("name_zh", "")
    return f"{chinese} · {english}" if chinese else english


@dataclass(frozen=True)
class EfpExpressionRecord:
    input_id: str
    msu_locus: str
    data_source: str
    data_source_label: str
    rap_locus: str = ""
    submitted_id: str = ""
    id_namespace: str = "MSU"
    group: str = ""
    tissue: str = ""
    expression_level: float | None = None
    standard_deviation: float | None = None
    samples: str = ""
    experiment_url: str = ""
    probe_id: str = ""
    mode: str = EFP_MODE
    status: str = "matched"
    error: str = ""

    def summary_row(self) -> dict[str, object]:
        return asdict(self)


_thread_local = threading.local()
_cache_lock = threading.Lock()
_result_cache: dict[tuple[str, str], tuple[float, list[EfpExpressionRecord]]] = {}
_plot_lock = threading.Lock()


def canonicalize_msu_gene(value: str) -> str:
    candidate = str(value or "").strip().split(".", 1)[0]
    if not MSU_GENE_PATTERN.fullmatch(candidate):
        return ""
    return "LOC_Os" + candidate[6:8] + "g" + candidate[-5:]


def canonicalize_rap_gene(value: str) -> str:
    candidate = str(value or "").strip()
    candidate = re.sub(r"^Os(\d{2})t(\d{7})(?:-\d+)?$", r"Os\1g\2", candidate, flags=re.I)
    if not RAP_GENE_PATTERN.fullmatch(candidate):
        return ""
    return "Os" + candidate[2:4] + "g" + candidate[-7:]


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": "MyBioTools/1.5 (+local research utility)"})
    return session


def get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = create_session()
        _thread_local.session = session
    return session


def parse_efp_result_html(html: str) -> tuple[str, str]:
    """Return the ephemeral expression-table URL and selected probe identifier."""
    link_match = _TABLE_LINK_PATTERN.search(html)
    table_url = urljoin(EFP_OUTPUT_ROOT, link_match.group(1)) if link_match else ""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    probe_match = _PROBE_PATTERN.search(text)
    return table_url, probe_match.group(1) if probe_match else ""


def _float_or_none(value: str) -> float | None:
    cleaned = value.strip().replace(",", "")
    if not cleaned or cleaned.casefold() in {"na", "n/a", "none", "nan"}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_expression_table_html(
    html: str,
    input_id: str,
    msu_locus: str,
    data_source: str,
    probe_id: str = "",
) -> list[EfpExpressionRecord]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[EfpExpressionRecord] = []
    for tr in soup.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 4:
            continue
        values = [cell.get_text(" ", strip=True) for cell in cells]
        expression = _float_or_none(values[2])
        deviation = _float_or_none(values[3])
        if expression is None:
            continue
        link = cells[5].find("a", href=True) if len(cells) > 5 else None
        rows.append(
            EfpExpressionRecord(
                input_id=input_id,
                msu_locus=msu_locus,
                data_source=data_source,
                data_source_label=EFP_DATA_SOURCES.get(data_source, data_source),
                group=values[0],
                tissue=values[1],
                expression_level=expression,
                standard_deviation=deviation,
                samples=values[4] if len(values) > 4 else "",
                experiment_url=urljoin(EFP_URL, str(link.get("href", ""))) if link else "",
                probe_id=probe_id,
            )
        )
    return rows


def _error_record(input_id: str, msu_locus: str, data_source: str, message: str) -> EfpExpressionRecord:
    rap_locus = canonicalize_rap_gene(input_id)
    is_single_cell = data_source == "rice_single_cell"
    return EfpExpressionRecord(
        input_id=input_id,
        msu_locus=msu_locus,
        data_source=data_source,
        data_source_label=EFP_DATA_SOURCES.get(data_source, data_source),
        rap_locus=rap_locus,
        submitted_id=rap_locus if is_single_cell else canonicalize_msu_gene(msu_locus),
        id_namespace="RAP" if is_single_cell else "MSU",
        status="failed",
        error=message,
    )


def fetch_efp_records(
    input_id: str,
    msu_locus: str,
    data_source: str,
    timeout: int = 20,
    rap_locus: str = "",
) -> list[EfpExpressionRecord]:
    msu_gene = canonicalize_msu_gene(msu_locus)
    rap_gene = canonicalize_rap_gene(rap_locus or input_id)
    is_single_cell = data_source == "rice_single_cell"
    submitted_id = rap_gene if is_single_cell else msu_gene
    namespace = "RAP" if is_single_cell else "MSU"
    if is_single_cell and not rap_gene:
        return [_error_record(input_id, msu_locus, data_source, "Single-cell eFP 需要 OsXXgXXXXXXX 格式的 RAP gene ID")]
    if not is_single_cell and not msu_gene:
        return [_error_record(input_id, msu_locus, data_source, "eFP 需要 LOC_Osxxgxxxxx 格式的 MSU gene ID")]
    if data_source not in EFP_DATA_SOURCES:
        return [_error_record(input_id, msu_gene, data_source, "未知的 eFP 数据源")]

    session = get_session()
    try:
        response = session.post(
            EFP_URL,
            data={
                "dataSource": data_source,
                "mode": EFP_MODE,
                "primaryGene": submitted_id,
                "secondaryGene": "LOC_Os06g10770",
                "threshold": "500",
            },
            timeout=timeout,
        )
        response.raise_for_status()
        table_url, probe_id = parse_efp_result_html(response.text)
        if not table_url:
            page_text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
            message = "官网未返回定量表"
            if "not found" in page_text.casefold() or "invalid" in page_text.casefold():
                message += "；该 ID 可能未映射到当前数据集的 probe"
            return [_error_record(input_id, msu_gene, data_source, message)]
        table_response = session.get(table_url, timeout=timeout)
        table_response.raise_for_status()
        records = parse_expression_table_html(
            table_response.text,
            input_id=input_id,
            msu_locus=msu_gene,
            data_source=data_source,
            probe_id=probe_id,
        )
        records = [EfpExpressionRecord(**{
            **record.summary_row(),
            "rap_locus": rap_gene,
            "submitted_id": submitted_id,
            "id_namespace": namespace,
        }) for record in records]
        return records or [_error_record(input_id, msu_gene, data_source, "eFP 定量表为空或无法解析")]
    except requests.RequestException as exc:
        return [_error_record(input_id, msu_gene, data_source, f"网络请求失败：{exc}")]
    except Exception as exc:
        return [_error_record(input_id, msu_gene, data_source, f"解析失败：{type(exc).__name__}: {exc}")]


def cached_fetch_efp_records(
    input_id: str,
    msu_locus: str,
    data_source: str,
    timeout: int = 20,
    rap_locus: str = "",
) -> list[EfpExpressionRecord]:
    query_id = canonicalize_rap_gene(rap_locus or input_id) if data_source == "rice_single_cell" else canonicalize_msu_gene(msu_locus)
    key = (query_id.casefold(), data_source)
    now = time.monotonic()
    if key[0]:
        with _cache_lock:
            cached = _result_cache.get(key)
            if cached and cached[0] > now:
                return [
                    EfpExpressionRecord(**{**item.summary_row(), "input_id": input_id})
                    for item in copy.deepcopy(cached[1])
                ]
            if cached:
                _result_cache.pop(key, None)
    records = fetch_efp_records(input_id, msu_locus, data_source, timeout=timeout, rap_locus=rap_locus)
    if records and all(item.status == "matched" and not item.error for item in records) and key[0]:
        with _cache_lock:
            _result_cache[key] = (now + EFP_CACHE_SECONDS, copy.deepcopy(records))
    return records


ProgressCallback = Callable[[int, int, str], None]
ItemProgressCallback = Callable[[str, int, int, str, bool], None]


def batch_fetch_efp_records(
    targets: Iterable[tuple[str, str] | tuple[str, str, str]],
    data_sources: Iterable[str],
    max_workers: int = 2,
    progress_callback: ProgressCallback | None = None,
    item_progress_callback: ItemProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[EfpExpressionRecord]:
    normalized_targets = []
    for target in targets:
        input_id, msu, *rap = target
        normalized_targets.append((str(input_id), canonicalize_msu_gene(msu), canonicalize_rap_gene(rap[0] if rap else input_id)))
    unique_targets = list(dict.fromkeys(normalized_targets))
    sources = list(dict.fromkeys(data_sources))
    jobs = [(input_id, msu, rap, source) for input_id, msu, rap in unique_targets for source in sources]
    if not jobs:
        return []
    ordered: list[list[EfpExpressionRecord] | None] = [None] * len(jobs)
    source_totals = {source: sum(job[3] == source for job in jobs) for source in sources}
    source_completed = {source: 0 for source in source_totals}
    source_failed = {source: 0 for source in source_totals}
    workers = max(1, min(int(max_workers), 2, len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(cached_fetch_efp_records, input_id, msu, source, 20, rap): index
            for index, (input_id, msu, rap, source) in enumerate(jobs)
        }
        completed = 0
        for future in as_completed(futures):
            index = futures[future]
            input_id, msu, rap, source = jobs[index]
            records = future.result()
            ordered[index] = records
            completed += 1
            source_completed[source] += 1
            if any(record.status != "matched" or record.error for record in records):
                source_failed[source] += 1
            item_detail = f"{source_completed[source]}/{source_totals[source]}：{input_id}"
            if item_progress_callback:
                item_progress_callback(
                    source,
                    source_completed[source],
                    source_totals[source],
                    item_detail,
                    bool(source_failed[source]),
                )
            if progress_callback:
                progress_callback(completed, len(jobs), f"{input_id} · {EFP_DATA_SOURCES.get(source, source)}")
            if cancel_check and cancel_check():
                for pending in futures:
                    pending.cancel()
                break
    return [item for group in ordered if group for item in group]


def unique_expression_records(records: Iterable[EfpExpressionRecord]) -> list[EfpExpressionRecord]:
    """Remove exact official-table duplicates for summaries/plots while keeping raw exports unchanged."""
    unique: list[EfpExpressionRecord] = []
    seen: set[tuple[object, ...]] = set()
    for item in records:
        key = (
            item.input_id,
            item.msu_locus,
            item.data_source,
            item.submitted_id,
            item.group,
            item.tissue,
            item.expression_level,
            item.standard_deviation,
            item.samples,
            item.probe_id,
            item.status,
            item.error,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def duplicate_expression_count(records: Iterable[EfpExpressionRecord]) -> int:
    """Return the number of exact duplicate rows present in an official eFP table response."""
    rows = list(records)
    return len(rows) - len(unique_expression_records(rows))


def expression_top_rows(records: Iterable[EfpExpressionRecord], limit: int = 3) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], list[EfpExpressionRecord]] = {}
    for record in unique_expression_records(records):
        if record.status == "matched" and record.expression_level is not None:
            grouped.setdefault((record.msu_locus, record.data_source), []).append(record)
    output: list[dict[str, object]] = []
    for (msu, source), items in grouped.items():
        for rank, item in enumerate(sorted(items, key=lambda value: value.expression_level or float("-inf"), reverse=True)[:limit], 1):
            output.append(
                {
                    "msu_locus": msu,
                    "data_source": source,
                    "data_source_label": item.data_source_label,
                    "rank": rank,
                    "tissue": item.tissue,
                    "expression_level": item.expression_level,
                    "standard_deviation": item.standard_deviation,
                }
            )
    return output


def _figure_bytes(fig) -> tuple[bytes, bytes]:
    png = io.BytesIO()
    svg = io.BytesIO()
    fig.savefig(png, format="png", dpi=600, bbox_inches="tight", facecolor="white")
    fig.savefig(svg, format="svg", bbox_inches="tight", facecolor="white")
    return png.getvalue(), svg.getvalue()


_DEVELOPMENT_COLORS = {
    "Root": CVD_PALETTE[0],
    "Leaf": CVD_PALETTE[2],
    "Meristem": CVD_PALETTE[3],
    "Inflorescence": CVD_PALETTE[4],
    "Seed": CVD_PALETTE[1],
    "Other": OTHER,
}
_STRESS_COLORS = {
    "Control": "#7A8794",
    "Drought": CVD_PALETTE[1],
    "Salt": CVD_PALETTE[3],
    "Cold": CVD_PALETTE[5],
    "Heat": CVD_PALETTE[4],
    "Anoxia": CVD_PALETTE[0],
    "Other": OTHER,
}


def _display_tissue(value: str) -> str:
    return str(value or "Unknown").replace("_", " · ")


def _expression_group(item: EfpExpressionRecord) -> str:
    tissue = str(item.tissue or "").casefold().replace("_", " ")
    if "stress" in item.data_source.casefold() or item.data_source.startswith("riceanoxia"):
        first = tissue.split(maxsplit=1)[0].title() if tissue else "Other"
        return first if first in _STRESS_COLORS else "Other"
    if "seed" in tissue or "grain" in tissue or "embryo" in tissue:
        return "Seed"
    if "inflorescence" in tissue or "panicle" in tissue or "flower" in tissue or "stigma" in tissue:
        return "Inflorescence"
    if "leaf" in tissue:
        return "Leaf"
    if "root" in tissue:
        return "Root"
    if "sam" in tissue or "meristem" in tissue:
        return "Meristem"
    explicit = str(item.group or "").strip()
    return explicit if explicit and not explicit.isdigit() else "Other"


def build_bar_figure(records: list[EfpExpressionRecord]):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matched = [
        item
        for item in unique_expression_records(records)
        if item.status == "matched" and item.expression_level is not None
    ]
    if not matched:
        raise ValueError("No matched eFP expression values")
    labels = [_display_tissue(item.tissue) for item in matched]
    values = np.asarray([float(item.expression_level) for item in matched])
    errors = np.asarray([float(item.standard_deviation or 0.0) for item in matched])
    groups = [_expression_group(item) for item in matched]
    palette = _STRESS_COLORS if "stress" in matched[0].data_source.casefold() else _DEVELOPMENT_COLORS
    colors = [palette.get(group, OTHER) for group in groups]
    height = max(3.1, min(8.6, 0.31 * len(matched) + 1.75))
    with publication_context():
        fig, ax = plt.subplots(figsize=(6.75, height), constrained_layout=True)
        positions = np.arange(len(matched))
        for index in range(1, len(groups)):
            if groups[index] != groups[index - 1]:
                ax.axhline(index - 0.5, color="#D7DEE6", linewidth=0.7, zorder=0)
        ax.errorbar(
            values,
            positions,
            xerr=errors,
            fmt="none",
            ecolor="#4B5B6C",
            elinewidth=0.85,
            capsize=2.4,
            capthick=0.85,
            zorder=2,
        )
        ax.scatter(values, positions, s=34, c=colors, edgecolor="white", linewidth=0.75, zorder=3)
        ax.set_yticks(positions, labels)
        ax.invert_yaxis()
        maximum = max(float(np.max(values + errors)), 1.0)
        label_offset = maximum * 0.018
        ax.set_xlim(0, maximum * 1.13)
        for y, value, error in zip(positions, values, errors):
            ax.text(value + error + label_offset, y, f"{value:.2f}", va="center", fontsize=6.2, color=MUTED)
        ax.set_xlabel("Expression level (official BAR eFP value)")
        add_axis_title(
            ax,
            matched[0].msu_locus,
            f"{matched[0].data_source_label}  ·  official value; error bar = BAR SD field",
        )
        style_axis(ax, grid_axis="x")
        used_groups = list(dict.fromkeys(groups))
        from matplotlib.lines import Line2D

        handles = [
            Line2D([0], [0], marker="o", linestyle="", markersize=5.2, markerfacecolor=palette.get(group, OTHER), markeredgecolor="white", label=group)
            for group in used_groups
        ]
        ax.legend(
            handles=handles,
            loc="upper right",
            bbox_to_anchor=(1.0, 1.095),
            frameon=False,
            ncol=min(5, len(handles)),
            handletextpad=0.35,
            columnspacing=0.9,
        )
        return fig


def build_heatmap_figure(records: list[EfpExpressionRecord], data_source: str):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matched = [
        item
        for item in unique_expression_records(records)
        if item.data_source == data_source and item.status == "matched" and item.expression_level is not None
    ]
    if not matched:
        raise ValueError("No matched eFP expression values")
    genes = list(dict.fromkeys(item.msu_locus for item in matched))
    tissues = list(dict.fromkeys(item.tissue for item in matched))
    matrix = np.full((len(genes), len(tissues)), np.nan, dtype=float)
    gene_index = {value: index for index, value in enumerate(genes)}
    tissue_index = {value: index for index, value in enumerate(tissues)}
    for item in matched:
        matrix[gene_index[item.msu_locus], tissue_index[item.tissue]] = float(item.expression_level)
    masked = np.ma.masked_invalid(matrix)
    cmap = plt.get_cmap("viridis").copy()
    cmap.set_bad("#E4E7EC")
    width = max(6.7, min(13.5, 0.42 * len(tissues) + 3.0))
    height = max(3.0, min(9.5, 0.34 * len(genes) + 1.8))
    with publication_context():
        fig, ax = plt.subplots(figsize=(width, height), constrained_layout=True)
        image = ax.imshow(masked, cmap=cmap, aspect="auto", interpolation="nearest")
        ax.set_xticks(np.arange(len(tissues)), [_display_tissue(value) for value in tissues], rotation=40, ha="right")
        ax.set_yticks(np.arange(len(genes)), genes)
        ax.set_xlabel("Tissue / treatment")
        ax.set_ylabel("MSU locus")
        add_axis_title(ax, "Rice eFP expression matrix", EFP_DATA_SOURCES.get(data_source, data_source))
        colorbar = fig.colorbar(image, ax=ax, shrink=0.82, pad=0.02)
        colorbar.set_label("Expression level (official eFP value)")
        colorbar.outline.set_visible(False)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.set_facecolor(LIGHT)
        return fig


def build_efp_chart_artifacts(records: Iterable[EfpExpressionRecord]) -> dict[str, bytes]:
    """Return per-gene bars and per-dataset heatmaps as SVG and 600 dpi PNG."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    matched = [
        item
        for item in unique_expression_records(records)
        if item.status == "matched" and item.expression_level is not None
    ]
    artifacts: dict[str, bytes] = {}
    by_gene_source: dict[tuple[str, str], list[EfpExpressionRecord]] = {}
    for item in matched:
        by_gene_source.setdefault((item.msu_locus, item.data_source), []).append(item)
    with _plot_lock:
        for (gene, source), items in by_gene_source.items():
            fig = build_bar_figure(items)
            try:
                png, svg = _figure_bytes(fig)
            finally:
                plt.close(fig)
            stem = f"bar_{gene}_{source}"
            artifacts[f"{stem}.png"] = png
            artifacts[f"{stem}.svg"] = svg
        genes = {item.msu_locus for item in matched}
        if len(genes) > 1:
            for source in dict.fromkeys(item.data_source for item in matched):
                fig = build_heatmap_figure(matched, source)
                try:
                    png, svg = _figure_bytes(fig)
                finally:
                    plt.close(fig)
                stem = f"heatmap_{source}"
                artifacts[f"{stem}.png"] = png
                artifacts[f"{stem}.svg"] = svg
    return artifacts


__all__ = [
    "DEFAULT_EFP_DATA_SOURCES",
    "EFP_DATA_SOURCES",
    "EFP_SOURCE_GLOSSARY",
    "EFP_GUIDE_URL",
    "EFP_MAX_GENES",
    "EFP_URL",
    "EfpExpressionRecord",
    "batch_fetch_efp_records",
    "build_efp_chart_artifacts",
    "cached_fetch_efp_records",
    "canonicalize_msu_gene",
    "canonicalize_rap_gene",
    "duplicate_expression_count",
    "efp_source_display_label",
    "expression_top_rows",
    "fetch_efp_records",
    "parse_efp_result_html",
    "parse_expression_table_html",
    "unique_expression_records",
]
