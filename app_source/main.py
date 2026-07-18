"""My Bio Tools Streamlit entry point."""

from __future__ import annotations

from functools import lru_cache
import importlib

import streamlit as st

from app_ui import APP_VERSION, apply_app_style, render_sidebar_brand
from job_ui import render_sidebar_job_center
from tool_catalog import TOOL_GROUPS, functional_tools


@lru_cache(maxsize=None)
def load_tool_module(module_name: str):
    return importlib.import_module(module_name)


def remember_tool(name: str) -> None:
    recent = [item for item in st.session_state.get("recent_tools", []) if item != name]
    st.session_state.recent_tools = ([name] + recent)[:3]


def main() -> None:
    st.set_page_config(
        page_title="My Bio Tools",
        page_icon="🧬",
        layout="wide",
        initial_sidebar_state="expanded",
        menu_items=None,
    )
    apply_app_style()
    render_sidebar_brand()

    categories = list(TOOL_GROUPS)
    category = st.sidebar.radio("工作区", categories, key="navigation_category")
    definitions = TOOL_GROUPS[category]
    tool_names = [tool.name for tool in definitions]
    selected_name = st.sidebar.selectbox("选择工具", tool_names, key="navigation_tool")
    selected = next(tool for tool in definitions if tool.name == selected_name)

    st.sidebar.caption(selected.description)
    if selected.website_url:
        st.sidebar.markdown(
            f"**来源网站**  \n[{selected.website_url}]({selected.website_url})"
        )
    with st.sidebar:
        render_sidebar_job_center()
    recent = st.session_state.get("recent_tools", [])
    if recent:
        st.sidebar.divider()
        st.sidebar.caption("最近使用")
        for item in recent:
            st.sidebar.markdown(f"• {item}")
    st.sidebar.divider()
    tool_count = len(functional_tools())
    st.sidebar.caption(f"v{APP_VERSION} · 本地优先处理 · {tool_count} 个工具")
    st.sidebar.caption("软件开发：Wu Lab 团队")
    st.sidebar.caption("维护：ZhangS · 致谢：GanP")

    remember_tool(selected.name)
    try:
        module = load_tool_module(selected.module)
        run = getattr(module, "run", None)
        if not callable(run):
            raise AttributeError(f"模块 {selected.module} 未定义 run()")
        run()
    except (ModuleNotFoundError, AttributeError) as exc:
        st.error(f"工具加载失败：{exc}")
        st.info("请重新启动内置服务；若问题仍存在，请从“工具”菜单打开运行日志。")
    except Exception as exc:
        st.error(f"工具运行时发生异常：{type(exc).__name__}: {exc}")
        st.info("输入数据不会被修改。可调整参数后重试，或打开运行日志定位问题。")


if __name__ == "__main__":
    main()
