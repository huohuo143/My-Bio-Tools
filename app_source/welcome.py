"""Dashboard page."""

from __future__ import annotations

import html

import streamlit as st

from app_ui import page_header
from tool_catalog import TOOL_GROUPS, functional_tools


def _tool_cards() -> str:
    sections: list[str] = []
    for group_name, tools in TOOL_GROUPS.items():
        if group_name == "概览":
            continue
        cards: list[str] = []
        for tool in tools:
            mode_class = " online" if tool.requires_internet else ""
            mode_text = "需要联网" if tool.requires_internet else "本地处理"
            source = ""
            if tool.website_url:
                safe_url = html.escape(tool.website_url, quote=True)
                source = (
                    '<div class="bio-tool-source">来源网址：'
                    f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">{safe_url}</a>'
                    "</div>"
                )
            cards.append(
                '<div class="bio-tool-card">'
                '<div class="bio-tool-heading">'
                f'<span>{html.escape(tool.icon)}</span>'
                f'<span class="bio-tool-title">{html.escape(tool.name)}</span>'
                f'<span class="bio-tool-mode{mode_class}">{mode_text}</span>'
                "</div>"
                f'<div class="bio-tool-copy">{html.escape(tool.description)}</div>'
                f'<div class="bio-tool-copy"><strong>怎么做：</strong>{html.escape(tool.method or "提供说明与入口")}</div>'
                f'<div class="bio-tool-copy"><strong>得到：</strong>{html.escape(tool.outputs or "模块说明与运行入口")}</div>'
                f"{source}"
                "</div>"
            )
        sections.append(
            f'<h3 class="bio-tool-section">{html.escape(group_name)}</h3>'
            f'<div class="bio-tool-grid">{"".join(cards)}</div>'
        )
    return "".join(sections)


def run() -> None:
    page_header(
        "Research utility suite",
        "让重复的数据整理更快、更稳",
        "面向水稻功能基因组与常规分子生物学任务的本地科研工具箱。输入文件默认只在本机处理。",
        ["本地优先", "批量处理", "结果可下载"],
    )

    col1, col2, col3 = st.columns(3)
    tools = functional_tools()
    online_count = sum(tool.requires_internet for tool in tools)
    col1.metric("功能模块", str(len(tools)), "3 个工作区")
    col2.metric("内置水稻数据", "4 套", "RAP-DB / IRGSP")
    col3.metric("联网模块", str(online_count), "均标明来源网址")

    st.markdown(
        """
        <div class="bio-card-grid">
          <div class="bio-card">
            <div class="bio-card-icon">⌁</div>
            <div class="bio-card-title">生信小工具</div>
            <div class="bio-card-copy">完成序列检查、Primer3、FASTA 处理以及 RAP/MSU 标识转换。</div>
          </div>
          <div class="bio-card">
            <div class="bio-card-icon">♧</div>
            <div class="bio-card-title">水稻基因一站式分析</div>
            <div class="bio-card-copy">独立工作区直达序列、表达、多组学、预测与科研报告。</div>
          </div>
          <div class="bio-card">
            <div class="bio-card-icon">◎</div>
            <div class="bio-card-title">RiceData 基因信息批量检索</div>
            <div class="bio-card-copy">批量检索 RiceData 基因名称、数据库 ID 与功能信息。</div>
          </div>
          <div class="bio-card">
            <div class="bio-card-icon">✓</div>
            <div class="bio-card-title">可追溯结果</div>
            <div class="bio-card-copy">关键工具同步报告匹配数、缺失项和风险提示，便于复核后继续分析。</div>
          </div>
          <div class="bio-card">
            <div class="bio-card-icon">≡</div>
            <div class="bio-card-title">方法与数据说明</div>
            <div class="bio-card-copy">在左侧“概览”打开说明中心，可搜索 39 项工具、内部分析、工作流与 eFP 数据源说明。</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.subheader("全部模块说明")
    st.caption("每个模块均说明用途与运行方式；更完整的输入、方法、产出与解读边界可在“方法与数据说明中心”统一检索。")
    st.markdown(_tool_cards(), unsafe_allow_html=True)

    st.subheader("建议工作方式")
    st.markdown(
        """
        1. 在左侧选择工作区与工具。
        2. 先用少量数据确认 ID 规则和输出格式。
        3. 核对匹配数与缺失项，再下载完整结果。

        > 水稻 ID 请注意 RAP（`Os01g0100100`）与 MSU（`LOC_Os01g01010`）的注释体系差异。
        """
    )

    st.subheader("维护与致谢")
    st.markdown("**软件开发：** Wu Lab 团队  \n**维护：** ZhangS  \n**致谢：** GanP")
