#!/usr/bin/env python3
"""Live acceptance test for the UGA RGAP backend used by unified analysis."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = ROOT / "app_source"
sys.path.insert(0, str(APP_SOURCE))

from RGAP_sequence_downloader import (  # noqa: E402
    SEQUENCE_TYPES,
    build_download_zip,
    cached_fetch_rgap_sequence,
)


QUERY_ID = "LOC_Os10g33000.1"
EXPECTED_LENGTHS = {
    "genomic_length_nt": 2932,
    "cds_length_nt": 2103,
    "protein_length_aa": 700,
}


def digest(sequence: str) -> str:
    return hashlib.sha256(sequence.encode("ascii")).hexdigest()


def main() -> int:
    os.chdir(APP_SOURCE)
    record = cached_fetch_rgap_sequence(QUERY_ID)
    observed = {
        "genomic_length_nt": len(record.genomic_sequence),
        "cds_length_nt": len(record.cds_sequence),
        "protein_length_aa": record.protein_length,
    }
    checks = {
        "status_matched": record.status == "matched",
        "genomic_length_matches_expected": observed["genomic_length_nt"] == EXPECTED_LENGTHS["genomic_length_nt"],
        "cds_length_matches_expected": observed["cds_length_nt"] == EXPECTED_LENGTHS["cds_length_nt"],
        "protein_length_matches_expected": observed["protein_length_aa"] == EXPECTED_LENGTHS["protein_length_aa"],
        "reported_lengths_match_observed": (
            record.reported_genomic_length == observed["genomic_length_nt"]
            and record.reported_cds_length == observed["cds_length_nt"]
            and record.reported_protein_length == observed["protein_length_aa"]
        ),
        "all_three_sequence_types_present": all(
            (record.genomic_sequence, record.cds_sequence, record.protein_sequence)
        ),
        "protein_terminal_stop_retained": record.protein_sequence.endswith("*"),
        "no_validation_warning": not record.validation_note,
    }
    success = all(checks.values())
    report = {
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "query_id": QUERY_ID,
        "source_url": record.source_url,
        "status": record.status,
        "success": success,
        "expected_lengths": EXPECTED_LENGTHS,
        "observed_lengths": observed,
        "reported_lengths": {
            "genomic_length_nt": record.reported_genomic_length,
            "cds_length_nt": record.reported_cds_length,
            "protein_length_aa": record.reported_protein_length,
        },
        "headers": {
            "genomic": record.genomic_header,
            "cds": record.cds_header,
            "protein": record.protein_header,
        },
        "sequence_sha256": {
            "genomic": digest(record.genomic_sequence) if record.genomic_sequence else "",
            "cds": digest(record.cds_sequence) if record.cds_sequence else "",
            "protein": digest(record.protein_sequence) if record.protein_sequence else "",
        },
        "putative_function": record.putative_function,
        "validation_note": record.validation_note,
        "error": record.error,
        "checks": checks,
    }

    output_dir = ROOT / "analysis_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "rgap_live_validation_20260717.json"
    archive_path = output_dir / "rgap_LOC_Os10g33000_1_verified_sequences_20260717.zip"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if record.has_sequence:
        archive_path.write_bytes(build_download_zip([record], list(SEQUENCE_TYPES)))

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Validation report: {report_path}")
    if archive_path.is_file():
        print(f"Verified sequence bundle: {archive_path}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
