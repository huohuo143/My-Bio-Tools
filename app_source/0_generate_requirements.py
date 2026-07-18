#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自动扫描当前目录及子目录中的 Python 文件
提取 import 的第三方包，并生成 requirements.txt
"""

import os
import re
import sys
from pathlib import Path

# Python 内置模块（不需要写入 requirements）
builtin_modules = set(sys.builtin_module_names)
extra_stdlibs = {
    "os", "sys", "re", "time", "io", "csv", "json", "math", "glob", "subprocess",
    "pathlib", "datetime", "logging", "argparse", "shutil", "itertools",
    "functools", "collections", "threading", "multiprocessing", "tempfile",
    "typing", "urllib", "http", "socket", "base64", "zipfile", "gzip"
}
builtin_modules.update(extra_stdlibs)

def extract_imports(file_path):
    """从单个Python文件提取 import 模块名"""
    imports = set()
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if line.startswith("import ") or line.startswith("from "):
                modules = re.findall(r'^(?:from|import)\s+([\w\d_\.]+)', line)
                for m in modules:
                    root = m.split('.')[0]
                    if root not in builtin_modules:
                        imports.add(root)
    return imports

def main():
    st_dir = Path(".")
    all_imports = set()

    print("🔍 正在扫描 Python 文件...")
    for pyfile in st_dir.rglob("*.py"):
        # 跳过当前脚本自身
        if pyfile.name == Path(__file__).name:
            continue
        imports = extract_imports(pyfile)
        if imports:
            print(f"📄 {pyfile.name}: {', '.join(sorted(imports))}")
            all_imports.update(imports)

    # 常见包名映射修正（比如 bs4 -> beautifulsoup4）
    name_map = {"bs4": "beautifulsoup4", "cv2": "opencv-python"}
    final_imports = sorted(name_map.get(pkg, pkg) for pkg in all_imports)

    # 写入 requirements.txt
    with open("requirements.txt", "w", encoding="utf-8") as f:
        f.write("# 自动生成的依赖文件\n")
        for pkg in final_imports:
            f.write(pkg + "\n")

    print("\n✅ 已生成 requirements.txt")
    print("📦 依赖列表：", ", ".join(final_imports))

if __name__ == "__main__":
    main()
