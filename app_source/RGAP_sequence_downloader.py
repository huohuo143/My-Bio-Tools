"""Batch-download genomic, CDS and protein FASTA from the UGA RGAP website."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import io
import re
import threading
import time
from urllib.parse import urlencode
import zipfile

from bs4 import BeautifulSoup
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
import streamlit as st
from urllib3.util.retry import Retry

from app_ui import format_bytes, page_header, tool_website


BASE_URL = "https://rice.uga.edu/cgi-bin/sequence_display.cgi"
MAX_BATCH_SIZE = 100
MSU_ID_PATTERN = re.compile(
    r"^LOC_Os(?P<chromosome>0[1-9]|1[0-2])g(?P<locus>\d{5})(?P<isoform>\.\d+)?$",
    re.IGNORECASE,
)
DNA_ALPHABET = set("ACGTRYSWKMBDHVN")
PROTEIN_ALPHABET = set("ABCDEFGHIKLMNPQRSTVWXYZJUO*")
SEQUENCE_TYPES = {
    "Genomic Sequence": (
        "genomic_header",
        "genomic_sequence",
        "RGAP_genomic_sequences.fasta",
    ),
    "CDS": ("cds_header", "cds_sequence", "RGAP_CDS_sequences.fasta"),
    "Protein": (
        "protein_header",
        "protein_sequence",
        "RGAP_protein_sequences.fasta",
    ),
}
SUMMARY_COLUMNS = [
    "input_id",
    "locus_id",
    "status",
    "genomic_length_nt",
    "reported_genomic_length_nt",
    "cds_length_nt",
    "reported_cds_length_nt",
    "protein_length_aa",
    "reported_protein_length_aa",
    "putative_function",
    "validation_note",
    "error",
    "source_url",
]

_thread_local = threading.local()
_cache_lock = threading.Lock()
_result_cache: dict[str, tuple[float, "RGAPSequenceRecord"]] = {}
RESULT_CACHE_SECONDS = 900


@dataclass
class RGAPSequenceRecord:
    query_id: str
    locus_id: str = ""
    genomic_header: str = ""
    genomic_sequence: str = ""
    cds_header: str = ""
    cds_sequence: str = ""
    protein_header: str = ""
    protein_sequence: str = ""
    reported_genomic_length: int | None = None
    reported_cds_length: int | None = None
    reported_protein_length: int | None = None
    putative_function: str = ""
    status: str = "failed"
    validation_note: str = ""
    error: str = ""
    source_url: str = ""

    @property
    def protein_length(self) -> int:
        """Return amino-acid length without counting a terminal stop symbol."""
        return len(self.protein_sequence[:-1]) if self.protein_sequence.endswith("*") else len(self.protein_sequence)

    @property
    def has_sequence(self) -> bool:
        return any((self.genomic_sequence, self.cds_sequence, self.protein_sequence))

    def summary_row(self) -> dict[str, object]:
        return {
            "input_id": self.query_id,
            "locus_id": self.locus_id,
            "status": self.status,
            "genomic_length_nt": len(self.genomic_sequence) if self.genomic_sequence else None,
            "reported_genomic_length_nt": self.reported_genomic_length,
            "cds_length_nt": len(self.cds_sequence) if self.cds_sequence else None,
            "reported_cds_length_nt": self.reported_cds_length,
            "protein_length_aa": self.protein_length if self.protein_sequence else None,
            "reported_protein_length_aa": self.reported_protein_length,
            "putative_function": self.putative_function,
            "validation_note": self.validation_note,
            "error": self.error,
            "source_url": self.source_url,
        }


def canonicalize_msu_id(identifier: str) -> str | None:
    match = MSU_ID_PATTERN.fullmatch(identifier.strip())
    if match is None:
        return None
    isoform = match.group("isoform") or ""
    return f"LOC_Os{match.group('chromosome')}g{match.group('locus')}{isoform}"


def parse_rgap_ids(text: str) -> list[str]:
    """Parse newline, comma, semicolon or whitespace-delimited IDs, preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in re.split(r"[\s,;，；]+", text.strip()):
        value = raw.strip()
        if not value:
            continue
        canonical = canonicalize_msu_id(value)
        normalized = canonical or value
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    return result


def source_url(identifier: str) -> str:
    return f"{BASE_URL}?{urlencode({'orf': identifier})}"


def create_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": "MyBioTools/1.2 (+local research utility; RGAP sequence downloader)",
            "Accept": "text/html,application/xhtml+xml",
        }
    )
    return session


def get_session() -> requests.Session:
    session = getattr(_thread_local, "rgap_session", None)
    if session is None:
        session = create_session()
        _thread_local.rgap_session = session
    return session


def parse_fasta_block(block: str) -> tuple[str, str]:
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if not lines or not lines[0].startswith(">"):
        return "", ""
    header = lines[0][1:].strip()
    sequence = re.sub(r"\s+", "", "".join(lines[1:])).upper()
    return header, sequence


def _reported_length(page_text: str, label: str) -> int | None:
    match = re.search(rf"{re.escape(label)}\s*:\s*(\d+)", page_text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _sequence_block(soup: BeautifulSoup, label: str) -> tuple[str, str]:
    label_folded = label.casefold()
    for paragraph in soup.find_all("p"):
        if paragraph.get_text(" ", strip=True).casefold() != label_folded:
            continue
        block = paragraph.find_next("pre")
        if block is not None:
            return parse_fasta_block(block.get_text("\n"))
    return "", ""


def parse_rgap_sequence_html(html: str, query_id: str, url: str = "") -> RGAPSequenceRecord:
    """Parse one RGAP sequence page and validate its reported sequence lengths."""
    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)
    folded_text = page_text.casefold()
    record = RGAPSequenceRecord(query_id=query_id, source_url=url or source_url(query_id))

    if "sequence information not found" in folded_text or "locus or model name was not found" in folded_text:
        record.status = "not_found"
        record.error = "RGAP 未找到该 locus 或 gene model。"
        return record

    record.genomic_header, record.genomic_sequence = _sequence_block(soup, "Genomic Sequence")
    record.cds_header, record.cds_sequence = _sequence_block(soup, "CDS")
    record.protein_header, record.protein_sequence = _sequence_block(soup, "Protein")
    if not record.has_sequence:
        record.error = "网页未返回可识别的 Genomic Sequence、CDS 或 Protein FASTA。"
        return record

    locus_match = re.search(r"LOC_Os(?:0[1-9]|1[0-2])g\d{5}", record.genomic_header or page_text, flags=re.IGNORECASE)
    if locus_match:
        record.locus_id = canonicalize_msu_id(locus_match.group(0)) or locus_match.group(0)

    record.reported_genomic_length = _reported_length(page_text, "Genomic sequence length")
    record.reported_cds_length = _reported_length(page_text, "CDS length")
    record.reported_protein_length = _reported_length(page_text, "Protein length")
    function_match = re.search(
        r"Putative Function\s*:\s*(.*?)(?=\s+Genomic Sequence(?:\s|$))",
        page_text,
        flags=re.IGNORECASE,
    )
    if function_match:
        record.putative_function = function_match.group(1).strip()

    notes: list[str] = []
    length_checks = [
        ("Genomic", len(record.genomic_sequence), record.reported_genomic_length),
        ("CDS", len(record.cds_sequence), record.reported_cds_length),
        ("Protein", record.protein_length, record.reported_protein_length),
    ]
    for label, actual, reported in length_checks:
        if actual and reported is not None and actual != reported:
            notes.append(f"{label} 实际长度 {actual} 与网页报告 {reported} 不一致")

    invalid_genomic = sorted(set(record.genomic_sequence) - DNA_ALPHABET)
    invalid_cds = sorted(set(record.cds_sequence) - DNA_ALPHABET)
    invalid_protein = sorted(set(record.protein_sequence) - PROTEIN_ALPHABET)
    if invalid_genomic:
        notes.append("Genomic 含非常规字符：" + "".join(invalid_genomic))
    if invalid_cds:
        notes.append("CDS 含非常规字符：" + "".join(invalid_cds))
    if invalid_protein:
        notes.append("Protein 含非常规字符：" + "".join(invalid_protein))

    missing_types = [
        label
        for label, sequence in (
            ("Genomic Sequence", record.genomic_sequence),
            ("CDS", record.cds_sequence),
            ("Protein", record.protein_sequence),
        )
        if not sequence
    ]
    if missing_types:
        notes.append("网页未提供：" + "、".join(missing_types))
        record.status = "partial"
    else:
        record.status = "matched"
    record.validation_note = "；".join(notes)
    return record


def fetch_rgap_sequence(
    identifier: str,
    session: requests.Session | None = None,
    timeout: tuple[float, float] = (5.0, 30.0),
) -> RGAPSequenceRecord:
    canonical = canonicalize_msu_id(identifier)
    url = source_url(canonical or identifier)
    if canonical is None:
        return RGAPSequenceRecord(
            query_id=identifier,
            status="invalid_id",
            error="ID 不符合 MSU/RGAP 格式（例如 LOC_Os10g33000.1）。",
            source_url=url,
        )

    try:
        response = (session or get_session()).get(url, timeout=timeout)
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding or "utf-8"
        return parse_rgap_sequence_html(response.text, canonical, url)
    except requests.RequestException as exc:
        return RGAPSequenceRecord(
            query_id=canonical,
            status="request_failed",
            error=f"网络请求失败：{type(exc).__name__}: {exc}",
            source_url=url,
        )
    except Exception as exc:
        return RGAPSequenceRecord(
            query_id=canonical,
            status="parse_failed",
            error=f"网页解析失败：{type(exc).__name__}: {exc}",
            source_url=url,
        )


def cached_fetch_rgap_sequence(identifier: str) -> RGAPSequenceRecord:
    canonical = canonicalize_msu_id(identifier) or identifier
    now = time.monotonic()
    with _cache_lock:
        cached = _result_cache.get(canonical)
        if cached is not None and now - cached[0] <= RESULT_CACHE_SECONDS:
            return copy.deepcopy(cached[1])
    record = fetch_rgap_sequence(canonical)
    if record.status in {"matched", "partial", "not_found"}:
        with _cache_lock:
            _result_cache[canonical] = (now, copy.deepcopy(record))
    return record


def batch_fetch_rgap_sequences(identifiers: list[str], max_workers: int = 3) -> list[RGAPSequenceRecord]:
    """Fetch a polite, bounded batch and return records in input order."""
    if len(identifiers) > MAX_BATCH_SIZE:
        raise ValueError(f"单次最多处理 {MAX_BATCH_SIZE} 个 ID。")
    results: list[RGAPSequenceRecord | None] = [None] * len(identifiers)
    valid_jobs: list[tuple[int, str]] = []
    for index, identifier in enumerate(identifiers):
        if canonicalize_msu_id(identifier) is None:
            results[index] = fetch_rgap_sequence(identifier)
        else:
            valid_jobs.append((index, identifier))

    workers = max(1, min(int(max_workers), 4, len(valid_jobs) or 1))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(cached_fetch_rgap_sequence, identifier): index
            for index, identifier in valid_jobs
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                results[index] = RGAPSequenceRecord(
                    query_id=identifiers[index],
                    status="failed",
                    error=f"批量任务失败：{type(exc).__name__}: {exc}",
                    source_url=source_url(identifiers[index]),
                )
    return [record for record in results if record is not None]


def summary_frame(records: list[RGAPSequenceRecord]) -> pd.DataFrame:
    return pd.DataFrame([record.summary_row() for record in records], columns=SUMMARY_COLUMNS)


def format_fasta(records: list[RGAPSequenceRecord], sequence_type: str) -> str:
    if sequence_type not in SEQUENCE_TYPES:
        raise ValueError(f"未知序列类型：{sequence_type}")
    header_attr, sequence_attr, _ = SEQUENCE_TYPES[sequence_type]
    output = io.StringIO()
    seen: set[tuple[str, str]] = set()
    for record in records:
        header = str(getattr(record, header_attr))
        sequence = str(getattr(record, sequence_attr))
        if not header or not sequence or (header, sequence) in seen:
            continue
        seen.add((header, sequence))
        output.write(f">{header}\n")
        for index in range(0, len(sequence), 60):
            output.write(sequence[index:index + 60] + "\n")
    return output.getvalue()


def build_download_zip(
    records: list[RGAPSequenceRecord],
    selected_types: tuple[str, ...] | list[str],
) -> bytes:
    summary = summary_frame(records)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for sequence_type in selected_types:
            _, _, filename = SEQUENCE_TYPES[sequence_type]
            archive.writestr(filename, format_fasta(records, sequence_type).encode("utf-8"))
        archive.writestr(
            "RGAP_download_summary.csv",
            summary.to_csv(index=False).encode("utf-8-sig"),
        )
        manifest = (
            "My Bio Tools - RGAP sequence batch download\n"
            f"Generated (UTC): {datetime.now(timezone.utc).isoformat()}\n"
            f"Source: {BASE_URL}\n"
            f"Input IDs: {len(records)}\n"
            "Protein length validation excludes one terminal '*' stop symbol; FASTA retains it.\n"
        )
        archive.writestr("README.txt", manifest.encode("utf-8"))
    return output.getvalue()


def run() -> None:
    page_header(
        "Rice Genome Annotation Project",
        "RGAP 在线序列批量下载",
        "按 MSU/RGAP locus 或 gene model ID，从 rice.uga.edu 批量获取 Genomic Sequence、CDS 和 Protein，并打包为可追溯结果。",
        ["MSU LOC_Os ID", "三类 FASTA", "CSV + ZIP"],
    )
    tool_website(__name__)
    st.info(
        "该工具会联网访问 UGA Rice Genome Annotation Project 公共网页。"
        "建议先用 2–5 个 ID 验证；单次最多 100 个，最多 4 个并发请求。"
    )
    identifiers_text = st.text_area(
        "MSU/RGAP locus 或 gene model ID（每行一个）",
        height=180,
        placeholder="LOC_Os10g33000.1\nLOC_Os01g01010.1",
        help="支持 LOC_Os10g33000 或 LOC_Os10g33000.1；不会自动猜测 RAP OsXXg ID。",
    )
    selected_types = st.multiselect(
        "下载序列类型",
        list(SEQUENCE_TYPES),
        default=list(SEQUENCE_TYPES),
    )
    max_workers = st.slider(
        "并发请求数",
        min_value=1,
        max_value=4,
        value=3,
        help="为减轻公共网站负担，默认仅使用 3 个并发请求。",
    )

    if not st.button("从 RGAP 批量下载", type="primary"):
        return
    identifiers = parse_rgap_ids(identifiers_text)
    if not identifiers:
        st.error("请提供至少一个 MSU/RGAP ID。")
        return
    if len(identifiers) > MAX_BATCH_SIZE:
        st.error(f"本次输入 {len(identifiers)} 个 ID；单次最多处理 {MAX_BATCH_SIZE} 个。")
        return
    if not selected_types:
        st.error("请至少选择一种序列类型。")
        return

    started = time.perf_counter()
    with st.spinner(f"正在从 RGAP 获取 {len(identifiers)} 个 ID…"):
        records = batch_fetch_rgap_sequences(identifiers, max_workers=max_workers)
    elapsed = time.perf_counter() - started
    summary = summary_frame(records)
    complete = sum(record.status == "matched" for record in records)
    partial = sum(record.status == "partial" for record in records)
    failed = len(records) - complete - partial

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("输入 ID", f"{len(records):,}")
    m2.metric("完整成功", f"{complete:,}")
    m3.metric("部分成功", f"{partial:,}")
    m4.metric("失败", f"{failed:,}")
    m5.metric("处理时间", f"{elapsed:.2f} s")
    st.dataframe(summary, width="stretch", hide_index=True)

    available_records = [record for record in records if record.has_sequence]
    if available_records:
        zip_bytes = build_download_zip(records, selected_types)
        st.success(
            f"已获取 {len(available_records)} 个 ID 的可用序列；长度与网页报告值已逐条核对。"
        )
        st.download_button(
            f"下载 RGAP 批量结果 ZIP（{format_bytes(len(zip_bytes))}）",
            zip_bytes,
            file_name="RGAP_sequence_batch_download.zip",
            mime="application/zip",
            type="primary",
        )
        columns = st.columns(len(selected_types))
        for column, sequence_type in zip(columns, selected_types):
            fasta = format_fasta(records, sequence_type).encode("utf-8")
            _, _, filename = SEQUENCE_TYPES[sequence_type]
            if fasta:
                column.download_button(
                    f"下载 {sequence_type}",
                    fasta,
                    file_name=filename,
                    mime="text/plain",
                )
        with st.expander("预览首个成功记录"):
            first = available_records[0]
            for sequence_type in selected_types:
                preview = format_fasta([first], sequence_type)
                if preview:
                    st.markdown(f"**{sequence_type}**")
                    st.code("\n".join(preview.splitlines()[:12]), language=None)
    else:
        st.warning("没有获取到可下载序列；请根据 status 与 error 列检查 ID 或网络状态。")

    summary_csv = summary.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "下载任务汇总 CSV",
        summary_csv,
        file_name="RGAP_download_summary.csv",
        mime="text/csv",
    )


__all__ = [
    "BASE_URL",
    "MAX_BATCH_SIZE",
    "RGAPSequenceRecord",
    "SEQUENCE_TYPES",
    "batch_fetch_rgap_sequences",
    "build_download_zip",
    "canonicalize_msu_id",
    "fetch_rgap_sequence",
    "format_fasta",
    "parse_rgap_ids",
    "parse_rgap_sequence_html",
    "summary_frame",
]
