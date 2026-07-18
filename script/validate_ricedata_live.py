#!/usr/bin/env python3
"""Live acceptance test for the RiceData gene annotation tool."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = ROOT / "app_source"
sys.path.insert(0, str(APP_SOURCE))

from RiceData_crawler import cached_fetch_gene_records  # noqa: E402


QUERY_ID = "Os01g0100100"
EXPECTED_RAP = "Os01g0100100"
EXPECTED_MSU_PREFIX = "LOC_Os01g01010"


def main() -> int:
    os.chdir(APP_SOURCE)
    began_at = time.monotonic()
    rows = cached_fetch_gene_records(QUERY_ID, timeout=20, include_details=False)
    elapsed_seconds = time.monotonic() - began_at
    matched_rows = [row for row in rows if row.get("status") == "matched"]
    first = matched_rows[0] if matched_rows else {}

    checks = {
        "record_matched": bool(matched_rows),
        "gene_id_present": bool(first.get("GeneID")),
        "rap_locus_matches": first.get("RAP_Locus") == EXPECTED_RAP,
        "msu_locus_matches": str(first.get("MSU_Locus", "")).startswith(EXPECTED_MSU_PREFIX),
        "no_record_error": not first.get("error"),
    }

    app = AppTest.from_file(str(APP_SOURCE / "main.py"), default_timeout=90)
    app.run()
    app.sidebar.radio[0].set_value("水稻资源").run()
    app.sidebar.selectbox[0].set_value("RiceData 信息检索").run()
    app.text_area[0].set_value(QUERY_ID).run()
    app.button[0].click().run(timeout=90)
    metrics = {item.label: item.value for item in app.metric}
    ui_frame = app.dataframe[0].value if app.dataframe else None
    ui_checks = {
        "page_has_no_exception": not app.exception,
        "page_has_no_error": not app.error,
        "ui_input_count_is_one": metrics.get("输入 ID") == "1",
        "ui_has_returned_record": int(metrics.get("返回记录", "0").replace(",", "")) >= 1,
        "ui_failed_count_is_zero": metrics.get("失败记录") == "0",
        "ui_summary_status_matched": bool(
            ui_frame is not None
            and not ui_frame.empty
            and ui_frame.loc[0, "status"] == "matched"
        ),
        "ui_rap_mapping_is_correct": bool(
            ui_frame is not None
            and not ui_frame.empty
            and ui_frame.loc[0, "RAP_Locus"] == EXPECTED_RAP
        ),
    }

    success = all(checks.values()) and all(ui_checks.values())
    report = {
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": "https://www.ricedata.cn/gene/",
        "query_id": QUERY_ID,
        "mode": "fast_basic_information",
        "elapsed_seconds": round(elapsed_seconds, 3),
        "success": success,
        "record": first,
        "checks": checks,
        "ui_checks": ui_checks,
    }

    output_dir = ROOT / "analysis_results"
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "ricedata_live_validation_20260717.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"Validation report: {report_path}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
