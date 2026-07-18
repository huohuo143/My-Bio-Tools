"""Primer3-based PCR primer design with validation and paired output."""

from __future__ import annotations

import re

import pandas as pd
import primer3
import streamlit as st

from app_ui import page_header, tool_explanation


def normalize_dna_sequence(sequence: str) -> str:
    """Remove FASTA headers/whitespace and normalize a DNA template."""
    lines = [line.strip() for line in sequence.splitlines() if line.strip() and not line.startswith(">")]
    return re.sub(r"\s+", "", "".join(lines)).upper()


def validate_dna_sequence(sequence: str) -> list[str]:
    errors: list[str] = []
    if not sequence:
        errors.append("序列为空")
        return errors
    invalid = sorted(set(sequence) - set("ACGTN"))
    if invalid:
        errors.append("包含非 DNA 字符：" + " ".join(invalid))
    if len(sequence) < 60:
        errors.append("序列短于 60 nt，难以设计常规 PCR 引物")
    return errors


def design_primer_pairs(
    sequence_id: str,
    sequence: str,
    *,
    primer_num: int,
    min_size: int,
    opt_size: int,
    max_size: int,
    min_tm: float,
    opt_tm: float,
    max_tm: float,
    min_gc: float,
    max_gc: float,
    product_min: int,
    product_max: int,
    forward_start: int,
    forward_len: int,
    reverse_start: int,
    reverse_len: int,
) -> tuple[pd.DataFrame, dict[str, object]]:
    seq_args = {
        "SEQUENCE_ID": sequence_id,
        "SEQUENCE_TEMPLATE": sequence,
        "SEQUENCE_PRIMER_PAIR_OK_REGION_LIST": [
            (forward_start, forward_len, reverse_start, reverse_len)
        ],
    }
    global_args = {
        "PRIMER_NUM_RETURN": int(primer_num),
        "PRIMER_OPT_SIZE": int(opt_size),
        "PRIMER_MIN_SIZE": int(min_size),
        "PRIMER_MAX_SIZE": int(max_size),
        "PRIMER_OPT_TM": float(opt_tm),
        "PRIMER_MIN_TM": float(min_tm),
        "PRIMER_MAX_TM": float(max_tm),
        "PRIMER_MIN_GC": float(min_gc),
        "PRIMER_MAX_GC": float(max_gc),
        "PRIMER_THERMODYNAMIC_OLIGO_ALIGNMENT": 1,
        "PRIMER_PRODUCT_SIZE_RANGE": [[int(product_min), int(product_max)]],
        "PRIMER_MAX_POLY_X": 5,
        "PRIMER_INTERNAL_MAX_POLY_X": 5,
        "PRIMER_SALT_MONOVALENT": 50.0,
        "PRIMER_DNA_CONC": 50.0,
        "PRIMER_MAX_NS_ACCEPTED": 0,
        "PRIMER_MAX_SELF_ANY": 8,
        "PRIMER_MAX_SELF_END": 3,
        "PRIMER_PAIR_MAX_COMPL_ANY": 8,
        "PRIMER_PAIR_MAX_COMPL_END": 3,
        "PRIMER_GC_CLAMP": 1,
    }
    design_function = getattr(primer3.bindings, "design_primers", None)
    if design_function is None:
        design_function = primer3.bindings.designPrimers
    result = design_function(seq_args, global_args)

    rows: list[dict[str, object]] = []
    for index in range(int(result.get("PRIMER_PAIR_NUM_RETURNED", 0))):
        left_pos = result.get(f"PRIMER_LEFT_{index}", [None, None])
        right_pos = result.get(f"PRIMER_RIGHT_{index}", [None, None])
        rows.append(
            {
                "Pair": index + 1,
                "Forward name": f"{sequence_id}.F{index + 1}",
                "Forward sequence (5'-3')": result.get(f"PRIMER_LEFT_{index}_SEQUENCE", ""),
                "Forward Tm (°C)": round(float(result.get(f"PRIMER_LEFT_{index}_TM", 0)), 2),
                "Forward GC (%)": round(float(result.get(f"PRIMER_LEFT_{index}_GC_PERCENT", 0)), 2),
                "Reverse name": f"{sequence_id}.R{index + 1}",
                "Reverse sequence (5'-3')": result.get(f"PRIMER_RIGHT_{index}_SEQUENCE", ""),
                "Reverse Tm (°C)": round(float(result.get(f"PRIMER_RIGHT_{index}_TM", 0)), 2),
                "Reverse GC (%)": round(float(result.get(f"PRIMER_RIGHT_{index}_GC_PERCENT", 0)), 2),
                "Product size (bp)": int(result.get(f"PRIMER_PAIR_{index}_PRODUCT_SIZE", 0)),
                "Forward start": left_pos[0],
                "Reverse start": right_pos[0],
                "Pair penalty": round(float(result.get(f"PRIMER_PAIR_{index}_PENALTY", 0)), 3),
            }
        )
    return pd.DataFrame(rows), result


def run() -> None:
    page_header(
        "Molecular biology",
        "Primer3 PCR 引物设计",
        "对 DNA 模板进行规范化和参数检查，输出成对引物、Tm、GC、产物长度与 penalty，便于直接进入复核。",
        ["Primer3", "成对结果", "参数校验"],
    )
    tool_explanation(__name__)

    sequence_id = st.text_input("序列 ID", value="example_gene")
    raw_sequence = st.text_area(
        "DNA 序列",
        height=190,
        placeholder=">example_gene\nATGCGT...",
        help="可以直接粘贴纯序列或单条 FASTA；空格、换行与 FASTA 标题会自动清理。",
    )
    sequence = normalize_dna_sequence(raw_sequence)
    if sequence:
        gc = 100 * (sequence.count("G") + sequence.count("C")) / len(sequence)
        st.caption(f"模板长度 {len(sequence):,} nt · GC {gc:.1f}% · N {sequence.count('N'):,}")

    basic_tab, region_tab, advanced_tab = st.tabs(["核心参数", "候选区域", "高级参数"])
    with basic_tab:
        c1, c2, c3 = st.columns(3)
        with c1:
            primer_num = st.number_input("返回引物对", min_value=1, max_value=50, value=5)
            product_min = st.number_input("最小产物 (bp)", min_value=40, max_value=10_000, value=100)
        with c2:
            opt_size = st.number_input("最佳引物长度", min_value=16, max_value=35, value=20)
            product_max = st.number_input("最大产物 (bp)", min_value=50, max_value=20_000, value=500)
        with c3:
            opt_tm = st.number_input("最佳 Tm (°C)", min_value=40.0, max_value=80.0, value=60.0, step=0.5)
    with region_tab:
        template_length = max(len(sequence), 500)
        default_region_length = max(10, min(150, template_length // 3))
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            forward_start = st.number_input("Forward 起始", min_value=0, value=0)
        with c2:
            forward_len = st.number_input("Forward 可选长度", min_value=10, value=default_region_length)
        with c3:
            reverse_start = st.number_input("Reverse 起始", min_value=0, value=max(template_length - default_region_length, 0))
        with c4:
            reverse_len = st.number_input("Reverse 可选长度", min_value=10, value=default_region_length)
        st.caption("位置均为 0-based。Reverse 区域表示允许选择反向引物的模板区间。")
    with advanced_tab:
        c1, c2, c3 = st.columns(3)
        with c1:
            min_size = st.number_input("最小引物长度", min_value=10, max_value=35, value=18)
            max_size = st.number_input("最大引物长度", min_value=16, max_value=40, value=25)
        with c2:
            min_tm = st.number_input("最小 Tm (°C)", min_value=30.0, max_value=80.0, value=57.0, step=0.5)
            max_tm = st.number_input("最大 Tm (°C)", min_value=40.0, max_value=85.0, value=63.0, step=0.5)
        with c3:
            min_gc = st.number_input("最小 GC (%)", min_value=0.0, max_value=100.0, value=40.0)
            max_gc = st.number_input("最大 GC (%)", min_value=0.0, max_value=100.0, value=60.0)

    if not st.button("设计引物", type="primary"):
        return

    errors = validate_dna_sequence(sequence)
    if min_size > opt_size or opt_size > max_size:
        errors.append("引物长度应满足 min ≤ opt ≤ max")
    if min_tm > opt_tm or opt_tm > max_tm:
        errors.append("Tm 应满足 min ≤ opt ≤ max")
    if min_gc > max_gc:
        errors.append("最小 GC 不能大于最大 GC")
    if product_min > product_max:
        errors.append("最小产物长度不能大于最大产物长度")
    if sequence and forward_start + forward_len > len(sequence):
        errors.append("Forward 候选区域超出模板长度")
    if sequence and reverse_start + reverse_len > len(sequence):
        errors.append("Reverse 候选区域超出模板长度")
    if errors:
        st.error("；".join(errors))
        return

    try:
        with st.spinner("Primer3 正在搜索候选引物…"):
            frame, raw_result = design_primer_pairs(
                sequence_id.strip() or "sequence",
                sequence,
                primer_num=int(primer_num),
                min_size=int(min_size),
                opt_size=int(opt_size),
                max_size=int(max_size),
                min_tm=float(min_tm),
                opt_tm=float(opt_tm),
                max_tm=float(max_tm),
                min_gc=float(min_gc),
                max_gc=float(max_gc),
                product_min=int(product_min),
                product_max=int(product_max),
                forward_start=int(forward_start),
                forward_len=int(forward_len),
                reverse_start=int(reverse_start),
                reverse_len=int(reverse_len),
            )
    except Exception as exc:
        st.error(f"Primer3 运行失败：{exc}")
        return

    if frame.empty:
        st.warning("当前约束下未找到可用引物。建议扩大候选区域、放宽 Tm/GC 或产物长度范围。")
        explain = raw_result.get("PRIMER_PAIR_EXPLAIN", "")
        if explain:
            st.code(str(explain), language=None)
        return

    st.success(f"找到 {len(frame)} 对候选引物。")
    st.dataframe(frame, width="stretch", hide_index=True)
    st.download_button(
        "下载引物设计结果",
        frame.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{sequence_id.strip() or 'sequence'}_primer_pairs.csv",
        mime="text/csv",
        type="primary",
    )
