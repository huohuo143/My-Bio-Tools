#!/usr/bin/env python3
"""Reproducible performance checks for bundled mapping and FASTA workflows."""

from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import sys
import time

os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")


ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = ROOT / "app_source"
sys.path.insert(0, str(APP_SOURCE))

from RAP_MSU_convert import MAPPING_PATH, load_mapping_index  # noqa: E402
from rice_seq_extractor import FASTA_FILES, extract_bundled_sequences  # noqa: E402


def timed(function, *args):
    started = time.perf_counter()
    result = function(*args)
    return result, time.perf_counter() - started


def main() -> int:
    os.chdir(APP_SOURCE)
    load_mapping_index.clear()
    extract_bundled_sequences.clear()

    (rap_to_msu, msu_to_rap), mapping_seconds = timed(load_mapping_index, str(MAPPING_PATH))
    gene_path = str(FASTA_FILES["Gene genomic sequence"])
    (records, missing, scanned), fasta_seconds = timed(
        extract_bundled_sequences,
        gene_path,
        ("Os01g0100100",),
    )
    (_, _, _), cached_seconds = timed(
        extract_bundled_sequences,
        gene_path,
        ("Os01g0100100",),
    )

    report = {
        "mapping_load_seconds": round(mapping_seconds, 4),
        "rap_mapping_keys": len(rap_to_msu),
        "msu_mapping_keys": len(msu_to_rap),
        "gene_fasta_scan_seconds": round(fasta_seconds, 4),
        "gene_fasta_cached_seconds": round(cached_seconds, 4),
        "gene_fasta_scanned_records": scanned,
        "gene_fasta_matched_records": len(records),
        "gene_fasta_missing": missing,
        "process_max_rss_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if records and not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
