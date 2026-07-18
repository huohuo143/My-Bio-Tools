#!/usr/bin/env python3
"""Live acceptance test for quantitative Rice eFP retrieval and chart export."""

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import sys

from PIL import Image


ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = ROOT / "app_source"
sys.path.insert(0, str(APP_SOURCE))

from rice_efp import EFP_URL, build_efp_chart_artifacts, cached_fetch_efp_records  # noqa: E402


QUERY_ID = "LOC_Os01g01080"
DATA_SOURCES = ("rice_rma", "ricestress_rma")


def main() -> int:
    os.chdir(APP_SOURCE)
    rows = []
    source_checks: dict[str, dict[str, object]] = {}
    for source in DATA_SOURCES:
        records = cached_fetch_efp_records(QUERY_ID, QUERY_ID, source, timeout=25)
        rows.extend(records)
        matched = [item for item in records if item.status == "matched"]
        source_checks[source] = {
            "record_count": len(matched),
            "has_numeric_expression": any(item.expression_level is not None for item in matched),
            "has_standard_deviation": any(item.standard_deviation is not None for item in matched),
            "has_samples": any(bool(item.samples) for item in matched),
            "has_experiment_link": any(bool(item.experiment_url) for item in matched),
            "probe_ids": sorted({item.probe_id for item in matched if item.probe_id}),
            "errors": [item.error for item in records if item.error],
        }

    charts = build_efp_chart_artifacts(rows)
    png_names = sorted(name for name in charts if name.endswith(".png"))
    svg_names = sorted(name for name in charts if name.endswith(".svg"))
    dpi_values = []
    for name in png_names:
        with Image.open(io.BytesIO(charts[name])) as image:
            dpi_values.append(round(float(image.info.get("dpi", (0, 0))[0]), 2))

    checks = {
        "all_sources_return_records": all(item["record_count"] > 0 for item in source_checks.values()),
        "all_sources_have_numeric_expression": all(item["has_numeric_expression"] for item in source_checks.values()),
        "all_sources_have_sd": all(item["has_standard_deviation"] for item in source_checks.values()),
        "all_sources_have_samples": all(item["has_samples"] for item in source_checks.values()),
        "all_sources_have_experiment_links": all(item["has_experiment_link"] for item in source_checks.values()),
        "png_and_svg_created": len(png_names) == len(DATA_SOURCES) and len(svg_names) == len(DATA_SOURCES),
        "png_is_600_dpi": bool(dpi_values) and all(value >= 590 for value in dpi_values),
    }
    success = all(checks.values())
    report = {
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": EFP_URL,
        "query_id": QUERY_ID,
        "data_sources": list(DATA_SOURCES),
        "mode": "Absolute",
        "success": success,
        "source_checks": source_checks,
        "chart_files": sorted(charts),
        "png_dpi": dpi_values,
        "checks": checks,
    }
    output_dir = ROOT / "analysis_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "efp_live_validation_20260717.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Validation report: {report_path}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
