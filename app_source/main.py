"""My Bio Tools Streamlit entry point."""

from __future__ import annotations

from functools import lru_cache
import importlib
import os

import streamlit as st

from appearance_preferences import (
    APPEARANCE_LABELS,
    load_appearance_mode,
    normalize_appearance_mode,
    save_appearance_mode,
)
from app_ui import APP_VERSION, apply_app_style, render_sidebar_brand
from job_ui import render_sidebar_job_center
from model_preferences import start_saved_model_connection_test
from tool_catalog import TOOL_GROUPS, functional_tools

DIRECT_RICE_WORKSPACE = "水稻基因一站式分析"
RICEDATA_WORKSPACE = "RiceData 基因信息批量检索"
PROTECTED_WORKSPACES = {DIRECT_RICE_WORKSPACE, RICEDATA_WORKSPACE}


def rice_workspace_unlocked() -> bool:
    return os.environ.get("MY_BIO_TOOLS_ACCESS_MODE") == "authorized"


def render_registration_gate(workspace: str) -> None:
    st.title("🔒 需要登录解锁")
    st.info(f"“{workspace}”包含授权数据和水稻专用分析能力，需注册并通过审核后使用。")
    st.markdown(
        "请点击 APP 顶部的 **登录/注册**。登录成功后，APP 会自动解锁该工作区和加密多组学数据库。"
    )
    st.success("生信小工具无需注册，可直接从左侧“生信小工具”进入。")


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
    # The persisted model route is tested once per desktop process.  The probe
    # runs in a daemon thread and never delays the initial window rendering.
    start_saved_model_connection_test()
    if "app_appearance_mode" not in st.session_state:
        st.session_state.app_appearance_mode = load_appearance_mode()
    st.session_state.app_appearance_mode = normalize_appearance_mode(
        st.session_state.app_appearance_mode
    )
    apply_app_style(st.session_state.app_appearance_mode)
    render_sidebar_brand()

    selected_appearance = st.sidebar.radio(
        "昼夜视图",
        list(APPEARANCE_LABELS),
        format_func=APPEARANCE_LABELS.get,
        key="app_appearance_mode",
        horizontal=True,
    )
    if selected_appearance != st.session_state.get("saved_app_appearance_mode"):
        save_appearance_mode(selected_appearance)
        st.session_state.saved_app_appearance_mode = selected_appearance
    st.sidebar.caption("“跟随系统”会自动使用当前操作系统的浅色或深色外观。")

    categories = ["概览", DIRECT_RICE_WORKSPACE, "生信小工具", RICEDATA_WORKSPACE]
    category = st.sidebar.radio("工作区", categories, key="navigation_category")
    if category == DIRECT_RICE_WORKSPACE:
        selected = next(
            tool for tool in TOOL_GROUPS[RICEDATA_WORKSPACE] if tool.name == DIRECT_RICE_WORKSPACE
        )
        st.session_state.navigation_tool = selected.name
    else:
        definitions = [
            tool for tool in TOOL_GROUPS[category] if tool.name != DIRECT_RICE_WORKSPACE
        ]
        tool_names = [tool.name for tool in definitions]
        if len(definitions) == 1:
            selected = definitions[0]
            st.session_state.navigation_tool = selected.name
        else:
            if st.session_state.get("navigation_tool") not in tool_names:
                st.session_state.navigation_tool = tool_names[0]
            selected_name = st.sidebar.selectbox("选择工具", tool_names, key="navigation_tool")
            selected = next(tool for tool in definitions if tool.name == selected_name)

    locked_workspace = category in PROTECTED_WORKSPACES and not rice_workspace_unlocked()
    st.sidebar.caption(selected.description)
    if locked_workspace:
        st.sidebar.warning("需登录解锁")
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

    if locked_workspace:
        render_registration_gate(category)
        return

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
