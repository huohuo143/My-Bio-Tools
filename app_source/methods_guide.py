"""Searchable methods and data interpretation center for all visible analyses."""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from analysis_explanations import (
    DEEP_ANALYSIS_EXPLANATIONS,
    PREDICTOR_EXPLANATIONS,
    SEQUENCE_AND_RESOURCE_EXPLANATIONS,
    WORKFLOW_EXPLANATIONS,
)
from app_ui import page_header
from rice_efp import EFP_DATA_SOURCES, EFP_SOURCE_GLOSSARY, EFP_URL, efp_source_display_label
from tool_catalog import functional_tools


TOOL_DATA_NATURE = {
    "tool_a": "本地确定性统计",
    "primer_design": "本地序列设计",
    "extract_fasta": "本地确定性处理",
    "fasta_rename": "本地确定性处理",
    "RAP_MSU_convert": "注释体系映射",
    "RiceData_crawler": "数据库整理的已有知识",
    "rice_gene_analysis": "多来源证据整合",
}


def _tool_entries() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for tool in functional_tools():
        entries.append(
            {
                "entry_id": f"tool::{tool.module}",
                "section": "独立工具",
                "module": tool.name,
                "data_nature": TOOL_DATA_NATURE.get(tool.module, "本地/联网数据处理"),
                "inputs": tool.inputs or "按页面提示输入数据与参数",
                "source": tool.website_name or ("联网服务" if tool.website_url else "APP 内置本地处理"),
                "method": tool.method or tool.description,
                "outputs": tool.outputs or "页面结果与可下载文件",
                "boundary": tool.cautions or "请结合原始数据与任务目标复核。",
                "reference_url": tool.website_url or "",
                "source_key": "",
            }
        )
    return entries


def _internal_entries() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    groups = (
        ("序列与数据库", SEQUENCE_AND_RESOURCE_EXPLANATIONS),
        ("蛋白定位预测", PREDICTOR_EXPLANATIONS),
        ("深度分析", DEEP_ANALYSIS_EXPLANATIONS),
        ("工作流与报告", WORKFLOW_EXPLANATIONS),
    )
    for section, rows in groups:
        for index, row in enumerate(rows):
            entries.append(
                {
                    "entry_id": f"internal::{section}::{index}",
                    "section": section,
                    "module": row["module"],
                    "data_nature": row.get("data_nature", "多来源分析"),
                    "inputs": row.get("inputs", "一站式分析中已解析的基因或序列"),
                    "source": row["source"],
                    "method": row["method"],
                    "outputs": row["outputs"],
                    "boundary": row["boundary"],
                    "reference_url": "",
                    "source_key": "",
                }
            )
    return entries


def _efp_entries() -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for source_key in EFP_DATA_SOURCES:
        info = EFP_SOURCE_GLOSSARY[source_key]
        entries.append(
            {
                "entry_id": f"efp::{source_key}",
                "section": "eFP 数据源",
                "module": efp_source_display_label(source_key),
                "data_nature": f"表达定量 · {info['scale']}",
                "inputs": f"{info['id_namespace']} gene ID；APP 依数据源自动路由 ID",
                "source": f"{info['reference']}；范围：{info['scope']}",
                "method": (
                    f"{info['design']}。APP 请求 BAR Rice eFP Absolute 模式，"
                    "解析官方数值表并保留数据源、样本、probe、status/error 与链接。"
                ),
                "outputs": f"{info['outputs']}；重复/汇总结构：{info['replicate_note']}",
                "boundary": f"适合回答：{info['best_for']}。边界：{info['caution']}。",
                "reference_url": EFP_URL,
                "source_key": source_key,
            }
        )
    return entries


def method_entries() -> list[dict[str, str]]:
    """Return the complete user-facing method catalog."""
    return _tool_entries() + _internal_entries() + _efp_entries()


def _matches(entry: dict[str, str], query: str) -> bool:
    if not query:
        return True
    searchable = " ".join(value for key, value in entry.items() if key != "entry_id")
    return query.casefold() in searchable.casefold()


def _render_data_nature_legend() -> None:
    cards = (
        ("本地确定性处理", "同样输入和参数应得到同样结果；重点复核 ID、坐标与参数。"),
        ("数据库记录", "反映当前数据库/注释版本收录的知识；空值不必然等于无证据。"),
        ("表达定量", "仅在匹配的实验设计和数值尺度内比较；不跨 RMA、MAS5 或未标单位的来源直比绝对值。"),
        ("计算预测", "用于生成候选和优先级；不自动构成定位、互作、调控或功能的实验证据。"),
    )
    card_html = "".join(
        '<div class="bio-guide-item">'
        f'<div class="bio-guide-label">{html.escape(label)}</div>'
        f'<div class="bio-guide-copy">{html.escape(copy)}</div>'
        "</div>"
        for label, copy in cards
    )
    st.markdown(f'<div class="bio-guide-grid">{card_html}</div>', unsafe_allow_html=True)


def _render_entry(entry: dict[str, str]) -> None:
    cards = (
        ("所属类别", entry["section"]),
        ("数据性质", entry["data_nature"]),
        ("输入", entry["inputs"]),
        ("数据/来源", entry["source"]),
        ("APP 怎么做", entry["method"]),
        ("获得的数据", entry["outputs"]),
        ("解读与限制", entry["boundary"]),
    )
    card_html = "".join(
        f'<div class="bio-guide-item{" bio-guide-wide" if label == "解读与限制" else ""}">'
        f'<div class="bio-guide-label">{html.escape(label)}</div>'
        f'<div class="bio-guide-copy">{html.escape(copy)}</div>'
        "</div>"
        for label, copy in cards
    )
    st.markdown(f'<div class="bio-guide-grid">{card_html}</div>', unsafe_allow_html=True)
    links: list[str] = []
    if entry["reference_url"]:
        links.append(f"[官方来源]({entry['reference_url']})")
    if entry["source_key"]:
        config_url = f"https://bar.utoronto.ca/transcriptomics/efp_rice/data/{entry['source_key']}.xml"
        links.append(f"[BAR 官方配置 XML]({config_url})")
    if links:
        st.caption("·".join(links))


def run() -> None:
    page_header(
        "Methods & data guide",
        "方法与数据说明中心",
        "集中查看每个工具、一站式内部模块和 Rice eFP 数据源的输入、处理方法、产出与证据边界。",
        ["可搜索", "39 项说明", "证据分类", "可导出"],
    )

    entries = method_entries()
    sections = list(dict.fromkeys(entry["section"] for entry in entries))
    prediction_count = sum("预测" in entry["data_nature"] for entry in entries)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("说明条目", len(entries))
    c2.metric("独立工具", sum(entry["section"] == "独立工具" for entry in entries))
    c3.metric("eFP 数据源", sum(entry["section"] == "eFP 数据源" for entry in entries))
    c4.metric("计算预测", prediction_count)

    with st.expander("先分清：不同类型结果能支持什么结论", expanded=True):
        _render_data_nature_legend()

    left, right = st.columns([1.1, 1.9])
    with left:
        selected_sections = st.multiselect("类别", sections, default=sections)
    with right:
        query = st.text_input("搜索模块、数据源、输入或输出", placeholder="例如：启动子、NLS、干旱、VCF、SD")

    filtered = [
        entry
        for entry in entries
        if entry["section"] in selected_sections and _matches(entry, query.strip())
    ]
    st.caption(f"当前显示 {len(filtered)} / {len(entries)} 项；说明用于正确解读，不代替原始数据、全文或实验验证。")

    if not filtered:
        st.info("没有匹配条目。请缩短关键词或恢复类别筛选。")
        return

    labels = {entry["entry_id"]: f"{entry['module']}  ·  {entry['section']}" for entry in filtered}
    selected_id = st.selectbox(
        "查看单项详解",
        [entry["entry_id"] for entry in filtered],
        format_func=labels.__getitem__,
    )
    selected = next(entry for entry in filtered if entry["entry_id"] == selected_id)
    st.subheader(selected["module"])
    _render_entry(selected)

    with st.expander("查看当前筛选结果对照表"):
        frame = pd.DataFrame(filtered).rename(
            columns={
                "section": "类别",
                "module": "模块/数据源",
                "data_nature": "数据性质",
                "inputs": "输入",
                "source": "数据/来源",
                "method": "APP 怎么做",
                "outputs": "获得的数据",
                "boundary": "解读与限制",
            }
        )
        columns = ["类别", "模块/数据源", "数据性质", "输入", "数据/来源", "APP 怎么做", "获得的数据", "解读与限制"]
        st.dataframe(frame[columns], width="stretch", hide_index=True, height=430)
        st.download_button(
            "下载当前说明 CSV",
            frame[columns].to_csv(index=False).encode("utf-8-sig"),
            file_name="my_bio_tools_methods_and_data_guide.csv",
            mime="text/csv",
        )


__all__ = ["method_entries", "run"]
