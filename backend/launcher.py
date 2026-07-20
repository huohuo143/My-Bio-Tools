#!/usr/bin/env python3
"""Launch the bundled Streamlit service for a native desktop container."""

from __future__ import annotations

import argparse
import io
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import time

from license_gate import LicenseValidationError, require_license_from_environment
from omics_unlock import OmicsUnlockError, unlock_omics_database


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="My Bio Tools backend")
    parser.add_argument("--port", type=int)
    parser.add_argument("--app-dir", type=Path)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument(
        "--runtime-smoke-test",
        action="store_true",
        help="validate the bundled plotting runtime and exit",
    )
    return parser.parse_args()


def initialize_plotting_runtime() -> None:
    """Load the complete headless plotting stack before worker threads exist."""
    if sys.platform == "darwin":
        cache_root = Path.home() / "Library" / "Caches" / "top.aizs.my-bio-tools"
    elif os.name == "nt":
        cache_root = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "My Bio Tools" / "Cache"
    else:
        cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "my-bio-tools"
    matplotlib_cache = cache_root / "matplotlib"
    matplotlib_cache.mkdir(parents=True, exist_ok=True)
    # Override PyInstaller's disposable MPLCONFIGDIR so the font cache survives
    # backend restarts and subsequent launches stay fast.
    os.environ["MPLCONFIGDIR"] = str(matplotlib_cache)
    os.environ.setdefault("MPLBACKEND", "Agg")

    # pyparsing.testing imports unittest while Matplotlib initializes. Import it
    # explicitly on the main thread so frozen importers never discover it from
    # an analysis worker.
    import unittest  # noqa: F401
    import matplotlib

    matplotlib.use("Agg", force=True)
    data_path = Path(matplotlib.get_data_path())
    if not data_path.is_dir():
        raise RuntimeError(f"Matplotlib data directory is unavailable: {data_path}")

    import matplotlib.pyplot as plt

    figure = plt.figure(figsize=(0.5, 0.5))
    try:
        figure.add_subplot(111).plot([0, 1], [0, 1])
        png_output = io.BytesIO()
        figure.savefig(png_output, format="png", dpi=72)
        if not png_output.getvalue().startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Matplotlib smoke test did not generate a PNG")

        # Rice eFP exports both raster and vector figures. Saving SVG here is
        # deliberate: PyInstaller otherwise cannot see the backend's dynamic
        # import and a frozen build can pass PNG startup checks but fail later.
        svg_output = io.BytesIO()
        figure.savefig(svg_output, format="svg")
        if b"<svg" not in svg_output.getvalue()[:1024]:
            raise RuntimeError("Matplotlib smoke test did not generate an SVG")
    finally:
        plt.close(figure)


def initialize_interpretation_runtime() -> None:
    """Verify that the frozen build contains every safe interpretation route."""
    import codex_chatgpt
    import llm_providers
    import report_interpretation

    if codex_chatgpt.PROVIDER_CODEX_CHATGPT != "codex_chatgpt":
        raise RuntimeError("Unexpected Codex provider identifier")
    if report_interpretation.PROVIDER_CODEX_CHATGPT != codex_chatgpt.PROVIDER_CODEX_CHATGPT:
        raise RuntimeError("Codex provider wiring is inconsistent")
    required = set(codex_chatgpt.CODEX_RESPONSE_SCHEMA.get("required", []))
    if "executive_summary" not in required or "integrated_hypotheses" not in required:
        raise RuntimeError("Codex response schema is incomplete")
    expected_cloud_providers = {
        "deepseek_api",
        "doubao_ark_api",
        "zhipu_glm_api",
        "qwen_dashscope_api",
        "chatanywhere_api",
        "openai_compatible",
    }
    if set(llm_providers.CLOUD_API_PROVIDERS) != expected_cloud_providers:
        raise RuntimeError("Cloud provider registry is incomplete")
    exported_ids = {
        report_interpretation.PROVIDER_DEEPSEEK,
        report_interpretation.PROVIDER_DOUBAO,
        report_interpretation.PROVIDER_ZHIPU,
        report_interpretation.PROVIDER_QWEN,
        report_interpretation.PROVIDER_CHATANYWHERE,
        report_interpretation.PROVIDER_OPENAI_COMPATIBLE,
    }
    if exported_ids != expected_cloud_providers:
        raise RuntimeError("Cloud provider exports are inconsistent")


def watch_parent(parent_pid: int) -> None:
    """Exit promptly if the POSIX native container is no longer running."""
    if os.name != "posix":
        return
    while True:
        try:
            os.kill(parent_pid, 0)
        except ProcessLookupError:
            os._exit(0)
        except PermissionError:
            pass
        time.sleep(1)


def main() -> int:
    multiprocessing.freeze_support()
    args = parse_args()
    os.environ.setdefault("PYTHONNOUSERSITE", "1")

    app_dir: Path | None = None
    if not args.runtime_smoke_test:
        if args.app_dir is None or args.port is None:
            raise ValueError("--port and --app-dir are required when starting the service")
        app_dir = args.app_dir.expanduser().resolve()
        main_script = app_dir / "main.py"
        if not main_script.is_file():
            raise FileNotFoundError(f"Missing Streamlit entry point: {main_script}")
        if not 1 <= args.port <= 65535:
            raise ValueError(f"Invalid port: {args.port}")
        access_mode = os.environ.get("MY_BIO_TOOLS_ACCESS_MODE", "guest")
        if access_mode == "authorized":
            try:
                require_license_from_environment()
            except LicenseValidationError as exc:
                raise RuntimeError(f"Authorization rejected: {exc}") from exc
            try:
                unlock_omics_database(app_dir)
            except OmicsUnlockError as exc:
                raise RuntimeError(f"Omics database unlock rejected: {exc}") from exc
        elif access_mode != "guest":
            raise RuntimeError("Unsupported access mode")

    try:
        initialize_plotting_runtime()
        initialize_interpretation_runtime()
    except Exception as exc:
        raise RuntimeError(
            f"Bundled runtime initialization failed: {type(exc).__name__}: {exc}"
        ) from exc

    if args.runtime_smoke_test:
        print("Bundled plotting and interpretation runtime smoke test passed.")
        return 0

    assert app_dir is not None
    main_script = app_dir / "main.py"

    os.chdir(app_dir)
    sys.path.insert(0, str(app_dir))

    # The system allocator is stable for the small result tables used here and
    # avoids frozen-runtime allocator differences between desktop platforms.
    os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    os.environ.setdefault("STREAMLIT_SERVER_FILE_WATCHER_TYPE", "none")

    # macOS uses a lightweight parent watcher. The Windows WPF shell owns the
    # backend through a Job Object and intentionally does not pass this flag.
    if args.parent_pid and os.name == "posix":
        threading.Thread(
            target=watch_parent,
            args=(args.parent_pid,),
            name="native-parent-watch",
            daemon=True,
        ).start()

    from streamlit.web import cli as streamlit_cli

    sys.argv = [
        "streamlit",
        "run",
        str(main_script),
        "--server.address=127.0.0.1",
        f"--server.port={args.port}",
        "--server.headless=true",
        "--server.fileWatcherType=none",
        "--server.runOnSave=false",
        "--browser.gatherUsageStats=false",
        "--global.developmentMode=false",
    ]
    return int(streamlit_cli.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
