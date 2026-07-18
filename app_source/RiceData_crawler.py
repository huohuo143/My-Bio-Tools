"""Batch retrieval of rice gene annotations from RiceData."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import io
import re
import threading
import time
from typing import Callable, Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
import streamlit as st
from urllib3.util.retry import Retry

from app_ui import page_header, tool_explanation, tool_website


RESULT_COLUMNS = [
    "check", "GeneID", "GeneName", "GeneSymbol", "RAP_Locus", "MSU_Locus",
    "NCBI_Locus", "cDNAs", "RefSeq_Locus_Nucl", "RefSeq_Locus_Prot", "Uniprots",
    "突变体表型", "定位与克隆", "时空表达谱", "亚细胞定位", "生物学功能", "其他信息",
    "reference_count", "reference_ids", "status", "error",
]
DETAIL_KEYS = ["突变体表型", "定位与克隆", "时空表达谱", "亚细胞定位", "生物学功能"]
_thread_local = threading.local()
_result_cache_lock = threading.Lock()
_result_cache: dict[tuple[str, bool], tuple[float, list[dict[str, str]]]] = {}
RESULT_CACHE_SECONDS = 900


def parse_gene_ids(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for line in text.splitlines():
        value = line.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.35,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({"User-Agent": "MyBioTools/1.5 (+local research utility)"})
    return session


def get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = create_session()
        _thread_local.session = session
    return session


def parse_detail_html(html: str) -> dict[str, object]:
    soup = BeautifulSoup(html, "html.parser")
    cells = soup.find_all("td", style="padding: 5px; font-size: 14px")
    if not cells:
        return {**{key: "" for key in DETAIL_KEYS}, "其他信息": "", "reference_links": []}

    content = "\n".join(cell.get_text(" ", strip=True) for cell in cells)
    result = {key: "" for key in DETAIL_KEYS}
    result["其他信息"] = content
    for key in DETAIL_KEYS:
        match = re.search(rf"【{re.escape(key)}】\s*(.*?)(?=【|$)", content, flags=re.S)
        if match:
            result[key] = match.group(1).strip()
    links: list[dict[str, str]] = []
    for link in soup.find_all("a", href=True):
        href = str(link.get("href") or "")
        match = re.search(r"papers\.aspx\?id=(\d+)", href, flags=re.I)
        if not match:
            continue
        reference_id = match.group(1)
        item = {
            "reference_id": reference_id,
            "source_url": urljoin("https://www.ricedata.cn/gene/list/", href),
            "anchor_text": link.get_text(" ", strip=True),
        }
        if item not in links:
            links.append(item)
    result["reference_links"] = links
    return result


def parse_reference_html(html: str, reference_id: str, source_url: str) -> dict[str, object]:
    """Parse one RiceData paper page without inferring missing bibliographic fields."""
    soup = BeautifulSoup(html, "html.parser")
    text = " ".join(soup.get_text(" ", strip=True).split())
    doi_match = re.search(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", text, flags=re.I)
    pmid_match = re.search(r"(?:PMID\s*[:：]?\s*|pubmed/)(\d+)", text, flags=re.I)
    year_match = re.search(r"\b(?:19|20)\d{2}\b", text)
    title = ""
    for selector in ("h1", "h2", "h3", "strong", "b", "title"):
        for node in soup.select(selector):
            candidate = " ".join(node.get_text(" ", strip=True).split())
            if len(candidate) >= 20 and "RiceData" not in candidate:
                title = candidate
                break
        if title:
            break
    if not title:
        labels = soup.find_all(string=re.compile(r"题名|Title", re.I))
        for label in labels:
            parent = label.parent
            sibling = parent.find_next(["td", "div", "p", "span"]) if parent else None
            candidate = " ".join(sibling.get_text(" ", strip=True).split()) if sibling else ""
            if len(candidate) >= 20:
                title = re.sub(r"^(?:题名|Title)\s*[:：]?\s*", "", candidate, flags=re.I)
                break
    return {
        "reference_id": reference_id,
        "title": title,
        "doi": doi_match.group(0).rstrip(".,;)") if doi_match else "",
        "year": year_match.group(0) if year_match else "",
        "pmid": pmid_match.group(1) if pmid_match else "",
        "source_url": source_url,
        "status": "parsed",
        "error": "",
    }


def safe_extract(pattern: str, value: object) -> str:
    match = re.findall(pattern, str(value))
    return match[0] if match else ""


def cell_text(cell: object) -> str:
    return cell.get_text(" ", strip=True) if hasattr(cell, "get_text") else ""


def link_attribute(cell: object, attribute: str) -> str:
    if not hasattr(cell, "find"):
        return ""
    link = cell.find("a", attrs={attribute: True})
    if link is None:
        return ""
    return str(link.get(attribute, "")).strip().strip("'\"")


def href_identifier(cell: object, marker: str) -> str:
    if not hasattr(cell, "find_all"):
        return ""
    folded_marker = marker.casefold()
    for link in cell.find_all("a", href=True):
        href = str(link.get("href", ""))
        index = href.casefold().find(folded_marker)
        if index >= 0:
            value = href[index + len(marker):]
            return value.split("?", 1)[0].split("#", 1)[0].strip("/")
    return ""


def first_link_text(cell: object) -> str:
    if not hasattr(cell, "find_all"):
        return ""
    for link in cell.find_all("a"):
        value = link.get_text(" ", strip=True)
        if value:
            return value
    return ""


def empty_error_row(gene_id: str, message: str) -> dict[str, str]:
    row = {column: "" for column in RESULT_COLUMNS}
    row.update({"check": gene_id, "status": "failed", "error": message})
    return row


def fetch_gene_records(
    gene_id: str,
    timeout: int = 12,
    include_details: bool = True,
) -> list[dict[str, str]]:
    session = get_session()
    try:
        response = session.get(
            "https://www.ricedata.cn/gene/accessions_switch.aspx",
            params={"para": gene_id},
            timeout=timeout,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        value_cells = soup.find_all("td", style="border-bottom:1px solid silver")
        id_cells = soup.find_all("td", height="22")
        if not id_cells:
            return [empty_error_row(gene_id, "页面未返回可识别的基因记录")]

        rows: list[dict[str, str]] = []
        offset = 0
        for id_cell in id_cells:
            link = id_cell.find("a")
            gene_record_id = link.get_text(strip=True) if link else id_cell.get_text(strip=True)
            if not gene_record_id:
                continue
            group = value_cells[offset:offset + 9]
            offset += 9
            padded = list(group) + [""] * max(0, 9 - len(group))

            if include_details:
                detail_response = session.get(
                    f"https://www.ricedata.cn/gene/list/{gene_record_id}.htm",
                    timeout=timeout,
                )
                detail_response.raise_for_status()
                detail_response.encoding = detail_response.apparent_encoding or detail_response.encoding
                details = parse_detail_html(detail_response.text)
                reference_links = list(details.pop("reference_links", []))
                references: list[dict[str, object]] = []
                for reference_link in reference_links:
                    try:
                        reference_response = session.get(str(reference_link["source_url"]), timeout=timeout)
                        reference_response.raise_for_status()
                        reference_response.encoding = reference_response.apparent_encoding or reference_response.encoding
                        references.append(
                            parse_reference_html(
                                reference_response.text,
                                str(reference_link["reference_id"]),
                                str(reference_link["source_url"]),
                            )
                        )
                    except Exception as exc:
                        references.append({
                            **reference_link,
                            "title": "",
                            "doi": "",
                            "year": "",
                            "pmid": "",
                            "status": "failed",
                            "error": f"{type(exc).__name__}: {exc}",
                        })
            else:
                details = {**{key: "" for key in DETAIL_KEYS}, "其他信息": ""}
                references = []

            row = {
                "check": gene_id,
                "GeneID": gene_record_id,
                "GeneName": cell_text(padded[1]),
                "GeneSymbol": cell_text(padded[2].find("em")) if hasattr(padded[2], "find") else "",
                "RAP_Locus": link_attribute(padded[3], "name") or first_link_text(padded[3]),
                "MSU_Locus": link_attribute(padded[4], "orf") or first_link_text(padded[4]),
                "NCBI_Locus": link_attribute(padded[5], "term") or first_link_text(padded[5]),
                "cDNAs": first_link_text(padded[6]) or href_identifier(padded[6], "nuccore/"),
                "RefSeq_Locus_Nucl": href_identifier(padded[7], "nuccore/"),
                "RefSeq_Locus_Prot": href_identifier(padded[7], "protein/"),
                "Uniprots": first_link_text(padded[8]) or href_identifier(padded[8], "uniprot/"),
                **details,
                "reference_count": len(references),
                "reference_ids": ",".join(str(item.get("reference_id") or "") for item in references),
                "ricedata_references": references,
                "source_url": f"https://www.ricedata.cn/gene/list/{gene_record_id}.htm",
                "status": "matched",
                "error": "",
            }
            rows.append(row)
        return rows or [empty_error_row(gene_id, "页面记录为空")]
    except requests.RequestException as exc:
        return [empty_error_row(gene_id, f"网络请求失败：{exc}")]
    except Exception as exc:
        return [empty_error_row(gene_id, f"解析失败：{type(exc).__name__}: {exc}")]


def cached_fetch_gene_records(
    gene_id: str,
    timeout: int = 12,
    include_details: bool = True,
) -> list[dict[str, str]]:
    """Reuse successful results briefly without caching transient site failures."""
    key = (gene_id.strip().casefold(), include_details)
    now = time.monotonic()
    with _result_cache_lock:
        cached = _result_cache.get(key)
        if cached and cached[0] > now:
            return copy.deepcopy(cached[1])
        if cached:
            _result_cache.pop(key, None)

    rows = fetch_gene_records(gene_id, timeout=timeout, include_details=include_details)
    if rows and all(row.get("status") == "matched" and not row.get("error") for row in rows):
        with _result_cache_lock:
            _result_cache[key] = (now + RESULT_CACHE_SECONDS, copy.deepcopy(rows))
    return rows


def batch_fetch_gene_records(
    gene_ids: Iterable[str],
    include_details: bool,
    max_workers: int = 3,
    timeout: int = 12,
    progress_callback: Callable[[int, int, str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[dict[str, str]]:
    """Retrieve an ordered batch without depending on Streamlit UI state."""
    identifiers = list(dict.fromkeys(str(value).strip() for value in gene_ids if str(value).strip()))
    if not identifiers:
        return []
    ordered_results: list[list[dict[str, str]] | None] = [None] * len(identifiers)
    workers = max(1, min(int(max_workers), 4, len(identifiers)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(cached_fetch_gene_records, gene_id, timeout, include_details): index
            for index, gene_id in enumerate(identifiers)
        }
        completed = 0
        for future in as_completed(futures):
            index = futures[future]
            gene_id = identifiers[index]
            try:
                ordered_results[index] = future.result()
            except Exception as exc:
                ordered_results[index] = [
                    empty_error_row(gene_id, f"批量任务异常：{type(exc).__name__}: {exc}")
                ]
            completed += 1
            if progress_callback:
                progress_callback(completed, len(identifiers), gene_id)
            if cancel_check and cancel_check():
                for pending in futures:
                    pending.cancel()
                break
    return [row for group in ordered_results if group for row in group]


def run() -> None:
    page_header(
        "Online rice resource",
        "RiceData 基因信息检索",
        "批量整理 RiceData 的基因名称、RAP/MSU/NCBI/UniProt 标识与功能描述；失败项会保留输入 ID 和原因。",
        ["需要联网", "有限并发", "失败项可追踪"],
    )
    tool_explanation(__name__, expanded=True)
    tool_website(__name__)
    st.info("该工具会访问 RiceData 公共网页。建议先用 2–5 个 ID 验证格式，再进行批量任务。")

    gene_input = st.text_area(
        "基因 ID（每行一个）",
        height=170,
        placeholder="LOC_Os01g01010\nOs01g0100100",
    )
    uploaded_file = st.file_uploader("或上传 TXT", type=["txt"])
    query_mode = st.radio(
        "检索深度",
        ["快速基础信息", "完整功能信息"],
        horizontal=True,
        help="完整模式会为每条记录追加一次详情页请求，外站较慢时耗时会明显增加。",
    )
    st.caption(
        "快速基础信息：获取 GeneID、基因名称/符号，以及 RAP、MSU、NCBI、cDNA、RefSeq、UniProt 等标识，"
        "适合大批量 ID 映射；完整功能信息：在基础字段之外，为每条返回记录继续访问详情页，补充突变体表型、"
        "定位与克隆、时空表达谱、亚细胞定位和生物学功能，适合较小的重点基因列表。"
        "两种模式的导出列相同；快速模式中的功能详情列留空。"
    )
    concurrency = st.slider("并发请求数", min_value=1, max_value=4, value=3, help="较低并发可减少对公共网站的压力。")

    if not st.button("开始检索", type="primary"):
        return
    text = gene_input
    if not text.strip() and uploaded_file is not None:
        raw = uploaded_file.getvalue()
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    gene_ids = parse_gene_ids(text)
    if not gene_ids:
        st.error("请提供至少一个基因 ID。")
        return
    if len(gene_ids) > 200:
        st.error("单次任务最多 200 个 ID。请拆分批次，避免给公共网站造成过大压力。")
        return

    progress = st.progress(0)
    status = st.empty()
    include_details = query_mode == "完整功能信息"
    def update_progress(completed: int, total: int, gene_id: str) -> None:
        progress.progress(completed / total)
        status.caption(f"已完成 {completed}/{total}：{gene_id}")

    rows = batch_fetch_gene_records(
        gene_ids,
        include_details=include_details,
        max_workers=int(concurrency),
        progress_callback=update_progress,
    )
    frame = pd.DataFrame(rows, columns=RESULT_COLUMNS)
    failed = int((frame["status"] == "failed").sum()) if not frame.empty else len(gene_ids)
    matched = int((frame["status"] == "matched").sum()) if not frame.empty else 0
    m1, m2, m3 = st.columns(3)
    m1.metric("输入 ID", f"{len(gene_ids):,}")
    m2.metric("返回记录", f"{matched:,}")
    m3.metric("失败记录", f"{failed:,}")

    st.dataframe(frame, width="stretch", hide_index=True)
    csv_buffer = io.StringIO()
    frame.to_csv(csv_buffer, index=False)
    st.download_button(
        "下载 RiceData 结果",
        csv_buffer.getvalue().encode("utf-8-sig"),
        file_name="ricedata_gene_annotations.csv",
        mime="text/csv",
        type="primary",
    )
    if failed:
        st.warning("部分记录请求或解析失败；请查看 status/error 列后重试，不要把空白字段视为无注释。")
