"""Low-memory extraction from bundled IRGSP/RAP-DB FASTA resources."""

from __future__ import annotations

import gzip
import io
from pathlib import Path
import re
import time

from Bio import SeqIO
import streamlit as st

from app_ui import format_bytes, page_header


DATA_DIR = Path(__file__).resolve().parent / "data" / "Rice_Genome_Annotation_Project"
FASTA_FILES = {
    "CDS": DATA_DIR / "IRGSP-1.0_cds_2025-03-19.fasta.gz",
    "Transcript": DATA_DIR / "IRGSP-1.0_transcript_2025-03-19.fasta.gz",
    "Gene genomic sequence": DATA_DIR / "IRGSP-1.0_gene_2025-03-19.fasta.gz",
}
GENE_PATTERN = re.compile(r"Os\d{2}g\d{7}", re.IGNORECASE)
TRANSCRIPT_PATTERN = re.compile(r"Os\d{2}t\d{7}(?:-\d+)?", re.IGNORECASE)


def parse_rice_ids(text: str) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in text.splitlines():
        value = raw.strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def query_prefix(identifier: str) -> tuple[str, bool]:
    """Return RAP transcript key and whether the request is an exact isoform."""
    if GENE_PATTERN.fullmatch(identifier):
        return re.sub("g", "t", identifier, count=1, flags=re.IGNORECASE), False
    if TRANSCRIPT_PATTERN.fullmatch(identifier):
        return identifier, "-" in identifier
    return identifier, True


def record_matches(record_id: str, query: str) -> bool:
    key, exact = query_prefix(query)
    if exact:
        return record_id.casefold() == key.casefold()
    return record_id.casefold().startswith(key.casefold() + "-") or record_id.casefold() == key.casefold()


@st.cache_data(show_spinner=False, max_entries=12, ttl=3600)
def extract_bundled_sequences(
    fasta_path: str,
    requested_ids: tuple[str, ...],
) -> tuple[list[tuple[str, str, str]], list[str], int]:
    """Scan compressed FASTA once, caching only the small query result."""
    found_by_query: dict[str, bool] = {query: False for query in requested_ids}
    exact_queries: dict[str, list[str]] = {}
    prefix_queries: dict[str, list[str]] = {}
    for query in requested_ids:
        key, exact = query_prefix(query)
        target = exact_queries if exact else prefix_queries
        target.setdefault(key.casefold(), []).append(query)
    records: list[tuple[str, str, str]] = []
    scanned = 0
    with gzip.open(fasta_path, "rt", encoding="utf-8") as handle:
        for record in SeqIO.parse(handle, "fasta"):
            scanned += 1
            folded_id = record.id.casefold()
            base_id = folded_id.rsplit("-", 1)[0] if "-" in folded_id else folded_id
            matching_queries = exact_queries.get(folded_id, []) + prefix_queries.get(base_id, [])
            if matching_queries:
                records.append((record.id, record.description, str(record.seq)))
                for query in matching_queries:
                    found_by_query[query] = True
    missing = [query for query, found in found_by_query.items() if not found]
    return records, missing, scanned


def format_fasta(records: list[tuple[str, str, str]]) -> str:
    output = io.StringIO()
    for record_id, description, sequence in records:
        header = description if description.startswith(record_id) else f"{record_id} {description}".strip()
        output.write(f">{header}\n")
        for index in range(0, len(sequence), 60):
            output.write(sequence[index:index + 60] + "\n")
    return output.getvalue()


def run() -> None:
    page_header(
        "Rice annotation",
        "IRGSP 水稻序列提取",
        "从内置 RAP-DB/IRGSP-1.0 数据按 gene 或 transcript ID 提取序列；只缓存查询结果，不把整套基因组序列常驻内存。",
        ["离线数据", "低内存", "gene / CDS / transcript"],
    )

    gene_text = st.text_area(
        "RAP gene / transcript ID（每行一个）",
        height=170,
        placeholder="Os01g0100100\nOs01t0100200-01",
        help="输入 gene ID 时会返回该基因对应的全部 RAP transcript 模型。",
    )
    sequence_type = st.selectbox("序列类型", list(FASTA_FILES))

    if not st.button("提取序列", type="primary"):
        return
    requested_ids = parse_rice_ids(gene_text)
    if not requested_ids:
        st.error("请提供至少一个 RAP gene 或 transcript ID。")
        return
    invalid = [item for item in requested_ids if not (GENE_PATTERN.fullmatch(item) or TRANSCRIPT_PATTERN.fullmatch(item))]
    if invalid:
        st.error("以下 ID 不符合 RAP 格式，未强制猜测：" + "、".join(invalid[:8]))
        return

    fasta_path = FASTA_FILES[sequence_type]
    if not fasta_path.is_file():
        st.error(f"内置数据缺失：{fasta_path.name}")
        return

    started = time.perf_counter()
    with st.spinner(f"正在扫描 {sequence_type} 数据…"):
        records, missing, scanned = extract_bundled_sequences(str(fasta_path), tuple(requested_ids))
    elapsed = time.perf_counter() - started
    output_text = format_fasta(records)
    output_bytes = output_text.encode("utf-8")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("输入 ID", f"{len(requested_ids):,}")
    m2.metric("匹配序列", f"{len(records):,}")
    m3.metric("扫描记录", f"{scanned:,}")
    m4.metric("处理时间", f"{elapsed:.2f} s")

    if records:
        st.download_button(
            f"下载 {sequence_type} FASTA（{format_bytes(len(output_bytes))}）",
            output_bytes,
            file_name=f"IRGSP_{sequence_type.lower().replace(' ', '_')}_sequences.fasta",
            mime="text/plain",
            type="primary",
        )
        with st.expander("预览前 40 行"):
            st.code("\n".join(output_text.splitlines()[:40]), language=None)
    else:
        st.warning("没有匹配到序列。请确认输入为 RAP ID，而不是 MSU LOC_Os ID。")
    if missing:
        with st.expander(f"查看 {len(missing)} 个未匹配 ID"):
            st.code("\n".join(missing), language=None)
            st.download_button(
                "下载未匹配 ID",
                ("\n".join(missing) + "\n").encode("utf-8"),
                file_name="unmatched_rice_ids.txt",
                mime="text/plain",
            )
