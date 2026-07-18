#!/usr/bin/env python3
"""Generate a compact, inspectable v1.9.1 multi-omics preview export."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app_source"))

from lab_omics import build_lab_omics_artifacts, query_lab_omics  # noqa: E402
from report_builder import build_report_artifacts  # noqa: E402
from rice_gene_core import AnalysisBundle  # noqa: E402


DEFAULT_DATABASE = Path("/Volumes/FAFU/analysis_results/wulab_omics_app_v1/wulab_omics_v1.sqlite")
DEFAULT_OUTPUT = Path("/Volumes/FAFU/analysis_results/wulab_omics_app_v1/preview_exports")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--genes",
        nargs="+",
        default=["LOC_Os03g28330", "LOC_Os08g31410"],
        help="MSU loci to include in the preview",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = query_lab_omics(args.genes, args.database)
    charts, raw = build_lab_omics_artifacts(result)
    bundle = AnalysisBundle(
        mode="批量分析",
        input_type="MSU locus",
        inputs=list(result["msu_loci"]),
        mapping_rows=[
            {
                "input_id": locus,
                "input_type": "MSU locus",
                "resolved_rap_gene": "",
                "resolved_msu_id": locus,
                "mapping_count": 1,
                "status": "matched",
                "note": "Preview query; model/RAP mappings are retained in Lab_Omics_Differential",
                "error": "",
            }
            for locus in result["msu_loci"]
        ],
        lab_omics_datasets=list(result["datasets"]),
        lab_omics_comparisons=list(result["comparisons"]),
        lab_omics_samples=list(result["samples"]),
        lab_omics_differential=list(result["differential"]),
        lab_omics_profiles=list(result["profiles"]),
        lab_omics_status=list(result["status"]),
        analysis_options={
            "app_version": "1.9.1 (build 20)",
            "lab_omics_schema": result.get("database_schema", ""),
            "preview": True,
        },
        sources=["Wu Lab internal analysed-omics database · schema v1 · read-only"],
    )
    artifacts = build_report_artifacts(
        bundle,
        primary_name=str(result["msu_loci"][0]),
        deep_charts=charts,
        deep_raw_artifacts=raw,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    stem = str(artifacts["stem"])
    for extension in ("docx", "xlsx", "zip"):
        (args.output / f"{stem}.{extension}").write_bytes(artifacts[extension])
    chart_dir = args.output / "heatmaps"
    data_dir = args.output / "plotting_data"
    chart_dir.mkdir(exist_ok=True)
    data_dir.mkdir(exist_ok=True)
    for name, payload in charts.items():
        (chart_dir / Path(name).name).write_bytes(payload)
    for name, payload in raw.items():
        (data_dir / Path(name).name).write_bytes(payload)
    print(f"Preview output: {args.output}")
    print(f"Genes: {len(result['msu_loci'])}")
    print(f"Datasets: {len(result['datasets'])}")
    print(f"Differential rows: {len(result['differential'])}")
    print(f"Abundance rows: {len(result['profiles'])}")
    print(f"Chart files: {len(charts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
