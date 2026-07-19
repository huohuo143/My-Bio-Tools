#!/usr/bin/env python3
"""Smoke-test every visible Streamlit page without requiring user input."""

from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = ROOT / "app_source"
MAIN_SCRIPT = APP_SOURCE / "main.py"

PAGES = {
    "概览": ["工作台首页", "方法与数据说明中心"],
    "水稻基因一站式分析": ["水稻基因一站式分析"],
    "生信小工具": [
        "DNA 组成与质量检查",
        "Primer3 引物设计",
        "FASTA 序列提取",
        "FASTA ID 重命名",
        "RAP ↔ MSU ID 转换",
    ],
    "RiceData 基因信息批量检索": [
        "RiceData 信息检索",
    ],
}

REQUIRED_EXPLANATION_EXPANDERS = {
    "方法与数据说明中心": ["先分清：不同类型结果能支持什么结论"],
    "DNA 组成与质量检查": ["本模块如何工作、会得到什么"],
    "Primer3 引物设计": ["本模块如何工作、会得到什么"],
    "FASTA 序列提取": ["本模块如何工作、会得到什么"],
    "FASTA ID 重命名": ["本模块如何工作、会得到什么"],
    "RAP ↔ MSU ID 转换": ["本模块如何工作、会得到什么"],
    "RiceData 信息检索": ["本模块如何工作、会得到什么"],
    "水稻基因一站式分析": [
        "先看懂：整个一站式分析怎样工作、能得到什么",
        "蛋白定位预测：每个工具怎么做、得到什么",
        "六项深度模块：数据来源、处理方法、产出与证据边界",
        "Rice eFP 详解：APP 怎么获取数据，以及 12 个数据源分别代表什么",
    ],
}


def element_messages(elements: object) -> list[str]:
    return [str(getattr(element, "value", element)) for element in elements]


def check_page(category: str, tool: str) -> list[str]:
    app = AppTest.from_file(str(MAIN_SCRIPT), default_timeout=30)
    app.run()
    app.sidebar.radio[0].set_value(category).run()
    if app.sidebar.selectbox:
        app.sidebar.selectbox[0].set_value(tool).run()

    failures: list[str] = []
    errors = element_messages(app.error)
    exceptions = element_messages(app.exception)
    if errors:
        failures.append(f"errors={errors}")
    if exceptions:
        failures.append(f"exceptions={exceptions}")
    expander_labels = [str(item.label) for item in app.expander]
    for required in REQUIRED_EXPLANATION_EXPANDERS.get(tool, []):
        if required not in expander_labels:
            failures.append(f"missing explanation expander={required}")
    if tool == "水稻基因一站式分析":
        tab_labels = [str(item.label) for item in app.tabs]
        for required in ("单个数据源详解", "12 个数据源对照表"):
            if required not in tab_labels:
                failures.append(f"missing eFP explanation tab={required}")
    if tool == "方法与数据说明中心":
        if not app.selectbox or len(app.metric) < 4:
            failures.append("methods guide summary/detail controls missing")
        if not app.text_input:
            failures.append("methods guide search control missing")
        else:
            app.text_input[0].set_value("单细胞").run()
            captions = element_messages(app.caption)
            if not any("当前显示 1 / 39 项" in item for item in captions):
                failures.append("methods guide single-cell search did not return one item")
            detail = [item for item in app.selectbox if getattr(item, "label", "") == "查看单项详解"]
            if len(detail) != 1 or detail[0].value != "efp::rice_single_cell":
                failures.append("methods guide single-cell detail routing failed")
    return failures


def main() -> int:
    os.chdir(APP_SOURCE)
    sys.path.insert(0, str(APP_SOURCE))
    failures: list[str] = []

    for category, tools in PAGES.items():
        for tool in tools:
            page_failures = check_page(category, tool)
            label = f"{category} -> {tool}"
            if page_failures:
                failures.append(f"{label}: {'; '.join(page_failures)}")
                print(f"FAIL {label}")
            else:
                print(f"PASS {label}")

    if failures:
        print("\nPage smoke test failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("\nAll visible pages loaded without Streamlit errors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
