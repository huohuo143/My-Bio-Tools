"""Streamlit rendering helpers for the global background job center."""

from __future__ import annotations

import streamlit as st

from analysis_jobs import JOB_MANAGER, PHASE_LABELS, TERMINAL_STATUSES, ProgressItemSnapshot


STATUS_LABELS = {
    "pending": "等待中",
    "queued": "排队中",
    "running": "运行中",
    "completed": "已完成",
    "completed_with_warnings": "已完成·有警告",
    "failed": "失败",
    "cancelled": "已取消",
}


def render_progress_breakdown(items: tuple[ProgressItemSnapshot, ...]) -> None:
    """Render selected phases and their source/tool children as independent bars."""
    for item in items:
        heading, state = st.columns([4, 1])
        heading.markdown(f"**{item.label}**")
        state.caption(STATUS_LABELS.get(item.status, item.status))
        st.progress(item.progress, text=f"{item.progress:.0%} · {item.detail}")
        for child in item.children:
            child_heading, child_state = st.columns([4, 1])
            child_heading.caption(f"↳ {child.label}")
            child_state.caption(STATUS_LABELS.get(child.status, child.status))
            st.progress(child.progress, text=f"{child.progress:.0%} · {child.detail}")


def _open_job(job_id: str) -> None:
    st.session_state.navigation_category = "水稻基因一站式分析"
    st.session_state.navigation_tool = "水稻基因一站式分析"
    st.session_state.selected_rice_job_id = job_id


@st.fragment(run_every=1.0)
def render_sidebar_job_center() -> None:
    snapshots = JOB_MANAGER.snapshots()
    if not snapshots:
        return
    st.divider()
    st.caption("后台任务")
    active = [item for item in snapshots if item.status not in TERMINAL_STATUSES]
    terminal = [item for item in snapshots if item.status in TERMINAL_STATUSES]
    visible = [*active, *terminal[:3]][:6]
    for item in visible:
        label = STATUS_LABELS.get(item.status, item.status)
        if item.status == "queued" and item.queue_position:
            label += f" #{item.queue_position}"
        st.markdown(f"**{item.project_name}**  ")
        current = next((phase for phase in item.progress_items if phase.key == item.stage), None)
        if current is not None:
            st.caption(f"{label} · 当前：{current.label} {current.progress:.0%}")
        else:
            stage_label = "" if item.status in TERMINAL_STATUSES else PHASE_LABELS.get(item.stage, item.stage)
            st.caption(f"{label}" + (f" · {stage_label}" if stage_label else ""))
        st.caption(item.error or item.detail)
        st.button(
            "查看任务",
            key=f"sidebar_job_{item.job_id}",
            on_click=_open_job,
            args=(item.job_id,),
            use_container_width=True,
        )


__all__ = ["STATUS_LABELS", "render_progress_breakdown", "render_sidebar_job_center"]
