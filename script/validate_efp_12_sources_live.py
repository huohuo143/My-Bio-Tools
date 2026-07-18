#!/usr/bin/env python3
"""Live acceptance audit for all BAR Rice eFP sources and their explanations."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import requests


ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = ROOT / "app_source"
sys.path.insert(0, str(APP_SOURCE))

from rice_efp import (  # noqa: E402
    EFP_DATA_SOURCES,
    EFP_SOURCE_GLOSSARY,
    EFP_URL,
    batch_fetch_efp_records,
    duplicate_expression_count,
)


QUERY = ("Os01g0100100", "LOC_Os01g01010", "Os01g0100100")
CONFIG_ROOT = "https://bar.utoronto.ca/transcriptomics/efp_rice/data"


def main() -> int:
    session = requests.Session()
    session.headers.update({"User-Agent": "MyBioTools/1.7.2 live source audit"})
    landing = session.get(EFP_URL, timeout=25)
    landing.raise_for_status()
    official_sources = [
        option.get("value", "")
        for option in BeautifulSoup(landing.text, "html.parser").select('select[name="dataSource"] option')
        if option.get("value")
    ]

    source_details: dict[str, dict[str, object]] = {}
    for source in EFP_DATA_SOURCES:
        response = session.get(f"{CONFIG_ROOT}/{source}.xml", timeout=25)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        tissues = root.findall(".//tissue")
        samples = root.findall(".//sample")
        source_details[source] = {
            "config_url": response.url,
            "config_http": response.status_code,
            "config_tissue_rows": len(tissues),
            "config_sample_entries": len(samples),
            "config_tissue_names": [item.get("name", "") for item in tissues],
        }

    rows = batch_fetch_efp_records([QUERY], EFP_DATA_SOURCES, max_workers=2)
    for source in EFP_DATA_SOURCES:
        items = [item for item in rows if item.data_source == source]
        matched = [item for item in items if item.status == "matched"]
        source_details[source].update(
            {
                "matched_rows": len(matched),
                "exact_duplicate_rows": duplicate_expression_count(matched),
                "submitted_ids": sorted({item.submitted_id for item in matched}),
                "id_namespaces": sorted({item.id_namespace for item in matched}),
                "probe_ids": sorted({item.probe_id for item in matched if item.probe_id}),
                "errors": [item.error for item in items if item.error],
                "glossary_complete": all(
                    EFP_SOURCE_GLOSSARY[source].get(field)
                    for field in (
                        "name_zh",
                        "scope",
                        "design",
                        "scale",
                        "id_namespace",
                        "reference",
                        "replicate_note",
                        "best_for",
                        "outputs",
                        "caution",
                    )
                ),
            }
        )

    checks = {
        "catalog_matches_official_selector": set(EFP_DATA_SOURCES) == set(official_sources),
        "all_official_configs_accessible": all(item["config_http"] == 200 for item in source_details.values()),
        "all_configs_have_tissues": all(item["config_tissue_rows"] > 0 for item in source_details.values()),
        "all_sources_return_numeric_rows": all(item["matched_rows"] > 0 for item in source_details.values()),
        "all_explanations_complete": all(item["glossary_complete"] for item in source_details.values()),
        "single_cell_uses_rap": source_details["rice_single_cell"]["id_namespaces"] == ["RAP"],
        "other_sources_use_msu": all(
            source_details[source]["id_namespaces"] == ["MSU"]
            for source in EFP_DATA_SOURCES
            if source != "rice_single_cell"
        ),
    }
    report = {
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "official_selector_url": EFP_URL,
        "query": {"rap": QUERY[2], "msu": QUERY[1]},
        "success": all(checks.values()),
        "checks": checks,
        "official_sources": official_sources,
        "source_details": source_details,
        "interpretation_note": (
            "Official rows are retained in raw exports. Exact duplicates are removed only from Top summaries and plots. "
            "A zero BAR SD field does not prove absence of biological variation."
        ),
    }
    output_dir = ROOT / "analysis_results" / "v1.7.2_efp_source_validation"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "efp_12_source_live_validation_20260718.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": report["success"], "checks": checks}, ensure_ascii=False, indent=2))
    print(f"Validation report: {output_path}")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
