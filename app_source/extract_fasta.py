"""Extract records from plain or gzip-compressed FASTA files."""

from __future__ import annotations

import gzip
import io
from typing import BinaryIO, TextIO

from Bio import SeqIO
from Bio.SeqRecord import SeqRecord
import streamlit as st

from app_ui import format_bytes, page_header, tool_explanation


def parse_ids(text: str) -> list[str]:
    seen: set[str] = set()
    ids: list[str] = []
    for line in text.splitlines():
        value = line.strip().split(",", 1)[0].strip()
        if value and value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


def open_uploaded_fasta(uploaded_file) -> TextIO:
    """Open a Streamlit upload as UTF-8 FASTA, including .gz content."""
    uploaded_file.seek(0)
    name = getattr(uploaded_file, "name", "").lower()
    binary: BinaryIO = uploaded_file
    if name.endswith(".gz"):
        return io.TextIOWrapper(gzip.GzipFile(fileobj=binary, mode="rb"), encoding="utf-8")
    return io.TextIOWrapper(binary, encoding="utf-8")


def identifier_key(identifier: str, ignore_version: bool) -> str:
    key = identifier.strip()
    if ignore_version:
        key = key.split(".", 1)[0]
    return key


def extract_fasta_records(
    handle: TextIO,
    requested_ids: list[str],
    *,
    ignore_version: bool = False,
) -> tuple[list[SeqRecord], list[str], int]:
    """Stream one FASTA pass and return records, missing IDs and scanned count."""
    requested_keys = {identifier_key(item, ignore_version) for item in requested_ids}
    found_keys: set[str] = set()
    records: list[SeqRecord] = []
    scanned = 0
    for record in SeqIO.parse(handle, "fasta"):
        scanned += 1
        key = identifier_key(record.id, ignore_version)
        if key in requested_keys:
            records.append(record)
            found_keys.add(key)
    missing = [item for item in requested_ids if identifier_key(item, ignore_version) not in found_keys]
    return records, missing, scanned


def run() -> None:
    page_header(
        "Sequence utility",
        "FASTA 序列提取",
        "按序列 ID 定向扫描 FASTA，支持普通文件与 gzip 压缩文件，并输出未匹配 ID 清单。",
        ["FASTA / FA / GZ", "低内存扫描", "缺失项复核"],
    )
    tool_explanation(__name__)

    fasta_file = st.file_uploader("上传 FASTA 文件", type=["fa", "fasta", "fna", "faa", "gz"])
    mode = st.radio("ID 输入方式", ["直接输入", "上传 ID 文件"], horizontal=True)
    id_text = ""
    if mode == "直接输入":
        id_text = st.text_area("序列 ID（每行一个）", height=170, placeholder="Os01t0100100-01\nOs01t0100200-01")
    else:
        id_file = st.file_uploader("上传 ID 文件", type=["txt", "csv"])
        if id_file is not None:
            raw = id_file.getvalue()
            for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    id_text = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
    ignore_version = st.checkbox("忽略点号版本后缀", value=False, help="例如将 ABC.1 与 ABC 视为同一 ID；不会去除水稻 -01 转录本后缀。")

    if not st.button("提取序列", type="primary"):
        return
    if fasta_file is None:
        st.error("请先上传 FASTA 文件。")
        return
    requested_ids = parse_ids(id_text)
    if not requested_ids:
        st.error("请提供至少一个序列 ID。")
        return

    try:
        with st.spinner("正在单遍扫描 FASTA…"):
            handle = open_uploaded_fasta(fasta_file)
            try:
                records, missing, scanned = extract_fasta_records(
                    handle,
                    requested_ids,
                    ignore_version=ignore_version,
                )
            finally:
                handle.close()
    except (OSError, UnicodeError, ValueError) as exc:
        st.error(f"FASTA 读取失败：{exc}")
        return

    output = io.StringIO()
    SeqIO.write(records, output, "fasta")
    output_bytes = output.getvalue().encode("utf-8")
    total_bases = sum(len(record.seq) for record in records)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("请求 ID", f"{len(requested_ids):,}")
    m2.metric("匹配序列", f"{len(records):,}")
    m3.metric("扫描序列", f"{scanned:,}")
    m4.metric("输出碱基/残基", f"{total_bases:,}")

    if records:
        st.download_button(
            f"下载提取结果（{format_bytes(len(output_bytes))}）",
            output_bytes,
            file_name="extracted_sequences.fasta",
            mime="text/plain",
            type="primary",
        )
        with st.expander("预览前 40 行"):
            st.code("\n".join(output.getvalue().splitlines()[:40]), language=None)
    else:
        st.warning("没有匹配到序列，请检查 ID 是否包含转录本或版本后缀。")

    if missing:
        with st.expander(f"查看 {len(missing)} 个未匹配 ID"):
            st.code("\n".join(missing), language=None)
            st.download_button(
                "下载未匹配 ID",
                ("\n".join(missing) + "\n").encode("utf-8"),
                file_name="unmatched_fasta_ids.txt",
                mime="text/plain",
            )
