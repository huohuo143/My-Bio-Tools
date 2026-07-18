"""Bidirectional conversion between RAP-DB and MSU rice gene identifiers."""

from __future__ import annotations

from pathlib import Path
import re

import pandas as pd
import streamlit as st

from app_ui import page_header, tool_explanation


MAPPING_PATH = Path(__file__).resolve().parent / "data" / "Rice_Genome_Annotation_Project" / "RAP-MSU_2025-03-19.txt.gz"
RAP_PATTERN = re.compile(r"Os\d{2}g\d{7}", re.IGNORECASE)
MSU_PATTERN = re.compile(r"LOC_Os\d{2}g\d{5}(?:\.\d+)?", re.IGNORECASE)


def parse_gene_ids(text: str) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for line in text.splitlines():
        for token in re.split(r"[,;\s]+", line.strip()):
            if token and token not in seen:
                seen.add(token)
                values.append(token)
    return values


def detect_id_type(gene_id: str) -> str:
    if RAP_PATTERN.fullmatch(gene_id):
        return "RAP"
    if MSU_PATTERN.fullmatch(gene_id):
        return "MSU"
    return "Unknown"


@st.cache_data(show_spinner=False)
def load_mapping_index(path: str = str(MAPPING_PATH)) -> tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    frame = pd.read_csv(path, sep="\t", header=None, names=["RAP", "MSU"], dtype=str).fillna("")
    rap_to_msu: dict[str, set[str]] = {}
    msu_to_rap: dict[str, set[str]] = {}

    for rap, msu_value in frame.itertuples(index=False):
        rap = rap.strip()
        if not rap or rap.casefold() == "none":
            continue
        msu_ids = [item.strip() for item in msu_value.split(",") if item.strip() and item.strip().casefold() != "none"]
        if not msu_ids:
            rap_to_msu.setdefault(rap, set())
            continue
        rap_to_msu.setdefault(rap, set()).update(msu_ids)
        for msu in msu_ids:
            msu_to_rap.setdefault(msu, set()).add(rap)
            msu_to_rap.setdefault(msu.split(".", 1)[0], set()).add(rap)

    return (
        {key: tuple(sorted(values)) for key, values in rap_to_msu.items()},
        {key: tuple(sorted(values)) for key, values in msu_to_rap.items()},
    )


def convert_gene_ids(
    gene_ids: list[str],
    rap_to_msu: dict[str, tuple[str, ...]],
    msu_to_rap: dict[str, tuple[str, ...]],
) -> pd.DataFrame:
    rap_lookup = {key.casefold(): (key, values) for key, values in rap_to_msu.items()}
    msu_lookup = {key.casefold(): (key, values) for key, values in msu_to_rap.items()}
    rows: list[dict[str, object]] = []
    for gene_id in gene_ids:
        id_type = detect_id_type(gene_id)
        if id_type == "RAP":
            _, converted = rap_lookup.get(gene_id.casefold(), (gene_id, ()))
            target_type = "MSU"
        elif id_type == "MSU":
            base = gene_id.split(".", 1)[0]
            _, converted = msu_lookup.get(
                gene_id.casefold(),
                msu_lookup.get(base.casefold(), (base, ())),
            )
            target_type = "RAP"
        else:
            converted = ()
            target_type = ""

        rows.append(
            {
                "input": gene_id,
                "input_type": id_type,
                "target_type": target_type,
                "converted": ",".join(converted),
                "mapping_count": len(converted),
                "status": "matched" if converted else "invalid_id" if id_type == "Unknown" else "not_mapped",
            }
        )
    return pd.DataFrame(rows)


def run() -> None:
    page_header(
        "Rice annotation",
        "RAP ↔ MSU ID 转换",
        "逐条识别 RAP 与 MSU 标识，允许两种体系混合输入，保留转录本一对多映射并标记未映射项。",
        ["混合 ID 输入", "一对多映射", "IRGSP-1.0"],
    )
    tool_explanation(__name__)

    input_method = st.radio("输入方式", ["直接输入", "上传 TXT"], horizontal=True)
    text = ""
    if input_method == "直接输入":
        text = st.text_area(
            "基因 ID（每行一个，也支持逗号分隔）",
            height=190,
            placeholder="Os01g0100100\nLOC_Os01g01019\nLOC_Os01g01030.1",
        )
    else:
        uploaded = st.file_uploader("上传 ID 列表", type=["txt", "csv"])
        if uploaded is not None:
            raw = uploaded.getvalue()
            for encoding in ("utf-8-sig", "utf-8", "gb18030"):
                try:
                    text = raw.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue

    if not st.button("开始转换", type="primary"):
        return
    gene_ids = parse_gene_ids(text)
    if not gene_ids:
        st.error("请提供至少一个基因 ID。")
        return
    if not MAPPING_PATH.is_file():
        st.error(f"内置 RAP–MSU 对照表缺失：{MAPPING_PATH.name}")
        return

    with st.spinner("正在读取内置对照表…"):
        rap_to_msu, msu_to_rap = load_mapping_index()
        result = convert_gene_ids(gene_ids, rap_to_msu, msu_to_rap)

    matched = int((result["status"] == "matched").sum())
    invalid = int((result["status"] == "invalid_id").sum())
    unmapped = len(result) - matched - invalid
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("输入 ID", f"{len(result):,}")
    m2.metric("成功映射", f"{matched:,}")
    m3.metric("未映射", f"{unmapped:,}")
    m4.metric("格式未知", f"{invalid:,}")

    st.dataframe(result, width="stretch", hide_index=True)
    st.download_button(
        "下载转换结果",
        result.to_csv(index=False).encode("utf-8-sig"),
        file_name="rap_msu_id_conversion.csv",
        mime="text/csv",
        type="primary",
    )
    if invalid:
        st.warning("格式未知的 ID 未强制猜测。请确认其属于 RAP、MSU 或其他注释版本。")
