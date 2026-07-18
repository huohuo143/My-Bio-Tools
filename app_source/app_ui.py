"""Shared visual language and small UI helpers for My Bio Tools."""

from __future__ import annotations

import html

import streamlit as st


APP_VERSION = "1.9.1"


def apply_app_style() -> None:
    """Apply a compact scientific desktop theme to the Streamlit surface."""
    st.markdown(
        """
        <style>
        :root {
            --bio-ink: #172033;
            --bio-muted: #667085;
            --bio-line: rgba(23, 32, 51, 0.10);
            --bio-accent: #0f766e;
            --bio-accent-soft: rgba(15, 118, 110, 0.10);
            --bio-blue-soft: rgba(29, 78, 216, 0.08);
            --bio-card: rgba(255, 255, 255, 0.86);
        }

        [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at 92% 2%, rgba(15, 118, 110, 0.07), transparent 24rem),
                #f7f9fc;
        }
        [data-testid="stHeader"], [data-testid="stToolbar"], #MainMenu, footer {
            display: none !important;
        }
        [data-testid="stMainBlockContainer"] {
            max-width: 1180px;
            padding-top: 2.1rem;
            padding-bottom: 3rem;
        }
        [data-testid="stSidebar"] {
            border-right: 1px solid var(--bio-line);
            background: rgba(241, 245, 249, 0.94);
        }
        [data-testid="stSidebar"] > div:first-child {
            padding-top: 1.35rem;
        }
        [data-testid="stSidebar"] [data-testid="stRadio"] label,
        [data-testid="stSidebar"] [data-baseweb="select"] {
            font-size: 0.92rem;
        }
        h1, h2, h3 {
            color: var(--bio-ink);
            letter-spacing: -0.025em;
        }
        h1 { font-size: 2.05rem !important; }
        h2 { font-size: 1.35rem !important; }
        p, label, .stMarkdown { color: var(--bio-ink); }
        div[data-testid="stMetric"] {
            background: var(--bio-card);
            border: 1px solid var(--bio-line);
            border-radius: 14px;
            padding: 0.85rem 1rem;
            box-shadow: 0 8px 24px rgba(23, 32, 51, 0.04);
        }
        div[data-testid="stMetricLabel"] { color: var(--bio-muted); }
        div[data-testid="stDataFrame"], div[data-testid="stTable"] {
            border: 1px solid var(--bio-line);
            border-radius: 12px;
            overflow: hidden;
        }
        div[data-testid="stFileUploader"] section,
        div[data-testid="stTextArea"] textarea,
        div[data-testid="stTextInput"] input,
        div[data-baseweb="select"] > div {
            border-radius: 10px;
        }
        .stButton > button, .stDownloadButton > button {
            border-radius: 9px;
            min-height: 2.45rem;
            font-weight: 600;
        }
        .stButton > button[kind="primary"],
        .stDownloadButton > button[kind="primary"] {
            background: var(--bio-accent);
            border-color: var(--bio-accent);
        }
        .bio-brand {
            display: flex;
            align-items: center;
            gap: 0.75rem;
            padding: 0.15rem 0 1rem;
        }
        .bio-brand-mark {
            width: 2.4rem;
            height: 2.4rem;
            border-radius: 11px;
            display: grid;
            place-items: center;
            color: white;
            font-size: 1.25rem;
            background: #0f766e;
            box-shadow: 0 8px 18px rgba(15, 118, 110, 0.20);
        }
        .bio-brand-title { font-weight: 750; color: var(--bio-ink); line-height: 1.1; }
        .bio-brand-subtitle { color: var(--bio-muted); font-size: 0.75rem; margin-top: 0.2rem; }
        .bio-eyebrow {
            color: var(--bio-accent);
            font-size: 0.73rem;
            font-weight: 750;
            letter-spacing: 0.11em;
            text-transform: uppercase;
            margin-bottom: 0.3rem;
        }
        .bio-lead { color: var(--bio-muted); max-width: 760px; margin-top: -0.45rem; }
        .bio-card-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.85rem;
            margin: 1rem 0 1.4rem;
        }
        .bio-card {
            background: var(--bio-card);
            border: 1px solid var(--bio-line);
            border-radius: 15px;
            padding: 1.05rem 1.1rem;
            box-shadow: 0 8px 24px rgba(23, 32, 51, 0.04);
        }
        .bio-card-icon { font-size: 1.35rem; margin-bottom: 0.45rem; }
        .bio-card-title { font-weight: 700; color: var(--bio-ink); }
        .bio-card-copy { color: var(--bio-muted); font-size: 0.88rem; margin-top: 0.2rem; line-height: 1.5; }
        .bio-chip {
            display: inline-flex;
            align-items: center;
            padding: 0.28rem 0.58rem;
            border-radius: 999px;
            color: var(--bio-accent);
            background: var(--bio-accent-soft);
            font-size: 0.76rem;
            font-weight: 650;
            margin-right: 0.35rem;
        }
        .bio-note {
            border-left: 3px solid var(--bio-accent);
            background: var(--bio-accent-soft);
            border-radius: 0 10px 10px 0;
            padding: 0.75rem 0.9rem;
            color: var(--bio-ink);
            font-size: 0.9rem;
        }
        .bio-resource {
            margin: 0.9rem 0 1.25rem;
            padding: 0.78rem 0.9rem;
            border: 1px solid rgba(29, 78, 216, 0.16);
            border-radius: 11px;
            background: var(--bio-blue-soft);
            color: var(--bio-ink);
            font-size: 0.88rem;
            line-height: 1.55;
        }
        .bio-resource-label { color: var(--bio-muted); font-weight: 650; }
        .bio-resource a { color: #1d4ed8; overflow-wrap: anywhere; }
        .bio-tool-section { margin: 1.35rem 0 0.55rem; }
        .bio-tool-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.75rem;
            margin: 0.65rem 0 1rem;
        }
        .bio-tool-card {
            background: var(--bio-card);
            border: 1px solid var(--bio-line);
            border-radius: 13px;
            padding: 0.9rem 1rem;
        }
        .bio-tool-heading { display: flex; align-items: center; gap: 0.5rem; }
        .bio-tool-title { font-weight: 700; color: var(--bio-ink); }
        .bio-tool-copy {
            color: var(--bio-muted);
            font-size: 0.86rem;
            line-height: 1.5;
            margin-top: 0.32rem;
        }
        .bio-tool-mode {
            display: inline-flex;
            padding: 0.17rem 0.45rem;
            border-radius: 999px;
            color: var(--bio-accent);
            background: var(--bio-accent-soft);
            font-size: 0.7rem;
            font-weight: 700;
        }
        .bio-tool-mode.online { color: #1d4ed8; background: var(--bio-blue-soft); }
        .bio-tool-source { font-size: 0.77rem; margin-top: 0.4rem; }
        .bio-tool-source a { color: #1d4ed8; overflow-wrap: anywhere; }
        .bio-guide-grid {
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 0.72rem;
            margin: 0.15rem 0 0.35rem;
        }
        .bio-guide-item {
            background: rgba(255, 255, 255, 0.58);
            border: 1px solid var(--bio-line);
            border-radius: 11px;
            padding: 0.78rem 0.86rem;
        }
        .bio-guide-wide { grid-column: 1 / -1; }
        .bio-guide-label {
            color: var(--bio-accent);
            font-size: 0.75rem;
            font-weight: 750;
            letter-spacing: 0.04em;
            margin-bottom: 0.22rem;
        }
        .bio-guide-copy {
            color: var(--bio-muted);
            font-size: 0.86rem;
            line-height: 1.55;
        }
        @media (max-width: 760px) {
            .bio-card-grid, .bio-tool-grid, .bio-guide-grid { grid-template-columns: 1fr; }
            .bio-guide-wide { grid-column: auto; }
            [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; }
        }
        @media (prefers-color-scheme: dark) {
            :root {
                --bio-ink: #e7edf5;
                --bio-muted: #9ca9ba;
                --bio-line: rgba(231, 237, 245, 0.12);
                --bio-card: rgba(30, 41, 59, 0.80);
            }
            [data-testid="stAppViewContainer"] {
                background: radial-gradient(circle at 92% 2%, rgba(20, 184, 166, 0.08), transparent 24rem), #101722;
            }
            [data-testid="stSidebar"] { background: rgba(17, 24, 39, 0.96); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar_brand() -> None:
    st.sidebar.markdown(
        """
        <div class="bio-brand">
          <div class="bio-brand-mark">⌬</div>
          <div>
            <div class="bio-brand-title">My Bio Tools</div>
            <div class="bio-brand-subtitle">科研数据处理工作台</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def page_header(kicker: str, title: str, description: str, chips: list[str] | None = None) -> None:
    safe_chips = "".join(
        f'<span class="bio-chip">{html.escape(chip)}</span>' for chip in (chips or [])
    )
    st.markdown(
        f"""
        <div class="bio-eyebrow">{html.escape(kicker)}</div>
        <h1>{html.escape(title)}</h1>
        <p class="bio-lead">{html.escape(description)}</p>
        <div>{safe_chips}</div>
        """,
        unsafe_allow_html=True,
    )


def note(text: str) -> None:
    st.markdown(f'<div class="bio-note">{html.escape(text)}</div>', unsafe_allow_html=True)


def tool_explanation(module_name: str, *, expanded: bool = False) -> None:
    """Render a consistent input-method-output-boundary guide on a tool page."""
    from tool_catalog import TOOLS_BY_MODULE

    tool = TOOLS_BY_MODULE[module_name]
    items = (
        ("输入", tool.inputs or "按页面提示输入文本、文件或参数。"),
        ("APP 怎么做", tool.method or tool.description),
        ("获得的数据", tool.outputs or "页面结果与可下载文件。"),
        ("解读与限制", tool.cautions or "请结合原始数据和任务目标复核输出。"),
    )
    cards = "".join(
        '<div class="bio-guide-item">'
        f'<div class="bio-guide-label">{html.escape(label)}</div>'
        f'<div class="bio-guide-copy">{html.escape(copy)}</div>'
        "</div>"
        for label, copy in items
    )
    with st.expander("本模块如何工作、会得到什么", expanded=expanded):
        st.markdown(f'<div class="bio-guide-grid">{cards}</div>', unsafe_allow_html=True)


def tool_website(module_name: str) -> None:
    """Render a visible, clickable source URL for an online-backed module."""
    from tool_catalog import TOOLS_BY_MODULE

    tool = TOOLS_BY_MODULE[module_name]
    if not tool.website_url:
        return
    safe_name = html.escape(tool.website_name or "官方网站")
    safe_url = html.escape(tool.website_url, quote=True)
    st.markdown(
        f"""
        <div class="bio-resource">
          <span class="bio-resource-label">来源网站：</span>{safe_name}<br>
          <a href="{safe_url}" target="_blank" rel="noopener noreferrer">{safe_url}</a>
        </div>
        """,
        unsafe_allow_html=True,
    )


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"
