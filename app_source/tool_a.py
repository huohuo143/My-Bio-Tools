"""DNA sequence composition and quality summary."""

from __future__ import annotations

import io

from Bio import SeqIO
from Bio.Seq import Seq
import pandas as pd
import streamlit as st

from app_ui import page_header, tool_explanation


IUPAC_DNA = set("ACGTRYSWKMBDHVN-")


def parse_sequences(text: str) -> list[tuple[str, str]]:
    """Parse FASTA or treat plain DNA text as one sequence."""
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith(">"):
        return [(record.id, str(record.seq).upper()) for record in SeqIO.parse(io.StringIO(stripped), "fasta")]
    sequence = "".join(stripped.split()).upper()
    return [("sequence_1", sequence)] if sequence else []


def analyze_sequences(records: list[tuple[str, str]]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for sequence_id, raw_sequence in records:
        sequence = raw_sequence.upper().replace("U", "T")
        length = len(sequence)
        canonical = sum(sequence.count(base) for base in "ACGT")
        gc_count = sequence.count("G") + sequence.count("C")
        invalid = sorted(set(sequence) - IUPAC_DNA)
        rows.append(
            {
                "Sequence ID": sequence_id,
                "Length (nt)": length,
                "A": sequence.count("A"),
                "C": sequence.count("C"),
                "G": sequence.count("G"),
                "T": sequence.count("T"),
                "N": sequence.count("N"),
                "GC (%)": round(100 * gc_count / canonical, 2) if canonical else 0.0,
                "N (%)": round(100 * sequence.count("N") / length, 2) if length else 0.0,
                "Ambiguous bases": length - canonical,
                "Invalid characters": "".join(invalid),
                "Reverse complement": str(Seq(sequence).reverse_complement()) if not invalid else "",
            }
        )
    return pd.DataFrame(rows)


def normalized_fasta(records: list[tuple[str, str]]) -> str:
    output = io.StringIO()
    for sequence_id, sequence in records:
        clean = sequence.upper().replace("U", "T")
        output.write(f">{sequence_id}\n")
        for index in range(0, len(clean), 60):
            output.write(clean[index:index + 60] + "\n")
    return output.getvalue()


def run() -> None:
    page_header(
        "Sequence utility",
        "DNA 序列组成与质量检查",
        "分析单条 DNA 或多序列 FASTA 的长度、GC、N、模糊碱基和非法字符，并生成规范化 FASTA。",
        ["多序列 FASTA", "GC / N", "反向互补"],
    )
    tool_explanation(__name__)
    sequence_text = st.text_area(
        "粘贴 DNA 或 FASTA",
        height=220,
        placeholder=">OsGene1\nATGCGTNN...\n>OsGene2\nATGC...",
    )
    if not st.button("分析序列", type="primary"):
        return

    records = parse_sequences(sequence_text)
    if not records:
        st.error("请输入 DNA 序列或 FASTA。")
        return
    frame = analyze_sequences(records)
    total_length = int(frame["Length (nt)"].sum())
    invalid_count = int(frame["Invalid characters"].astype(bool).sum())
    weighted_gc = (
        sum((seq.upper().replace("U", "T").count("G") + seq.upper().replace("U", "T").count("C")) for _, seq in records)
        / max(sum(sum(seq.upper().replace("U", "T").count(base) for base in "ACGT") for _, seq in records), 1)
        * 100
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("序列数", f"{len(frame):,}")
    m2.metric("总长度", f"{total_length:,} nt")
    m3.metric("整体 GC", f"{weighted_gc:.2f}%")
    m4.metric("含非法字符序列", f"{invalid_count:,}")
    if invalid_count:
        st.warning("存在非 IUPAC DNA 字符；相应序列未生成反向互补，请先核对输入。")

    display_columns = [
        "Sequence ID", "Length (nt)", "GC (%)", "N (%)", "Ambiguous bases", "Invalid characters"
    ]
    st.dataframe(frame[display_columns], width="stretch", hide_index=True)
    c1, c2 = st.columns(2)
    with c1:
        st.download_button(
            "下载组成统计",
            frame.to_csv(index=False).encode("utf-8-sig"),
            file_name="sequence_composition_summary.csv",
            mime="text/csv",
            type="primary",
        )
    with c2:
        st.download_button(
            "下载规范化 FASTA",
            normalized_fasta(records).encode("utf-8"),
            file_name="normalized_sequences.fasta",
            mime="text/plain",
        )
