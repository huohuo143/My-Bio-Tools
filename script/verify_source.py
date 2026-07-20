#!/usr/bin/env python3
"""Verify that the staged Streamlit source and its required data are complete."""

from __future__ import annotations

import gzip
import importlib
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = ROOT / "app_source"
DATA_DIR = APP_SOURCE / "data" / "Rice_Genome_Annotation_Project"

HELPER_MODULES = [
    "app_ui",
    "appearance_preferences",
    "analysis_explanations",
    "tool_catalog",
    "rice_gene_core",
    "prediction_services",
    "prediction_visualization",
    "codex_chatgpt",
    "llm_providers",
    "mechanism_evidence",
    "model_preferences",
    "report_interpretation",
    "report_builder",
    "rice_efp",
    "analysis_jobs",
    "job_ui",
    "rice_seq_extractor",
    "RGAP_sequence_downloader",
    "rice_utr_promoter_downloader",
]

MODULES = [
    "welcome",
    "methods_guide",
    "tool_a",
    "primer_design",
    "extract_fasta",
    "fasta_rename",
    "RiceData_crawler",
    "RAP_MSU_convert",
    "rice_gene_analysis",
]

DATA_FILES = {
    "IRGSP-1.0_transcript_2025-03-19.fasta.gz": 20_000_000,
    "IRGSP-1.0_gene_2025-03-19.fasta.gz": 39_000_000,
    "IRGSP-1.0_cds_2025-03-19.fasta.gz": 12_000_000,
    "RAP-MSU_2025-03-19.txt.gz": 300_000,
}


def main() -> int:
    failures: list[str] = []
    sys.path.insert(0, str(APP_SOURCE))
    os.chdir(APP_SOURCE)

    main_script = APP_SOURCE / "main.py"
    if not main_script.is_file():
        failures.append(f"missing entry point: {main_script}")

    for module_name in HELPER_MODULES:
        try:
            importlib.import_module(module_name)
            print(f"helper {module_name}: ok")
        except Exception as exc:
            failures.append(f"{module_name}: {type(exc).__name__}: {exc}")

    for module_name in MODULES:
        try:
            module = importlib.import_module(module_name)
            if not callable(getattr(module, "run", None)):
                failures.append(f"{module_name}: run() is missing")
            else:
                print(f"module {module_name}: ok")
        except Exception as exc:
            failures.append(f"{module_name}: {type(exc).__name__}: {exc}")

    for filename, minimum_size in DATA_FILES.items():
        path = DATA_DIR / filename
        if not path.is_file():
            failures.append(f"missing data file: {path}")
            continue
        if path.stat().st_size < minimum_size:
            failures.append(
                f"data file is unexpectedly small: {path} ({path.stat().st_size} bytes)"
            )
            continue
        try:
            with gzip.open(path, "rt", encoding="utf-8", errors="replace") as handle:
                first_line = handle.readline().strip()
            if not first_line:
                failures.append(f"data file is empty after decompression: {path}")
            else:
                print(f"data {filename}: ok ({path.stat().st_size} bytes)")
        except Exception as exc:
            failures.append(f"{filename}: cannot read gzip content: {exc}")

    vendor_dir = APP_SOURCE / "vendor" / "nlstradamus"
    for path in (
        vendor_dir / "NLStradamus.cpp",
        vendor_dir / "README_C.txt",
        vendor_dir / "LICENSE_GPLv3.txt",
    ):
        if not path.is_file():
            failures.append(f"missing NLStradamus distribution file: {path}")
    binary = vendor_dir / "bin" / ("NLStradamus.exe" if os.name == "nt" else "NLStradamus")
    if not binary.is_file():
        failures.append(f"missing NLStradamus binary for current platform: {binary}")
    elif os.name != "nt" and not os.access(binary, os.X_OK):
        failures.append(f"NLStradamus binary is not executable: {binary}")
    else:
        print(f"NLStradamus binary: ok ({binary.name})")

    if failures:
        print("\nSource verification failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("\nSource verification passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
