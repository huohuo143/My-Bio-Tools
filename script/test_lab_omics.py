#!/usr/bin/env python3
"""Regression checks for the analysed Wu Lab multi-omics integration."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app_source"))

from lab_omics import (  # noqa: E402
    build_lab_omics_artifacts,
    canonical_msu_loci,
    project_profile_matrices,
    query_lab_omics,
)


DATABASE = Path(
    os.environ.get(
        "MY_BIO_TOOLS_OMICS_DB",
        "/Volumes/FAFU/analysis_results/wulab_omics_app_v1/wulab_omics_v1.sqlite",
    )
)
LEAFHOPPER_DATASET = "npb_whiteback_electric_leafhopper_rnaseq"


class LabOmicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not DATABASE.is_file():
            raise unittest.SkipTest(f"database unavailable: {DATABASE}")

    def test_msu_canonicalization_and_traceability(self) -> None:
        self.assertEqual(
            canonical_msu_loci(["LOC_Os03g28330.1", "prefix LOC_os08g31410 suffix"]),
            ["LOC_Os03g28330", "LOC_Os08g31410"],
        )
        result = query_lab_omics(["LOC_Os03g28330.1"], DATABASE)
        self.assertTrue(result["differential"])
        self.assertTrue(
            any(
                row.get("msu_model") and row.get("rap_gene") and row.get("original_id")
                for row in result["differential"]
            )
        )
        models = {
            row.get("msu_model")
            for row in result["differential"]
            if row.get("dataset_id") == LEAFHOPPER_DATASET
        }
        self.assertGreater(len(models), 1, "one-to-many MSU models must remain traceable")

    def test_leafhopper_order_and_four_replicates(self) -> None:
        result = query_lab_omics(["LOC_Os03g28330"], DATABASE)
        comparisons = [
            row for row in result["comparisons"] if row["dataset_id"] == LEAFHOPPER_DATASET
        ]
        for treatment, prefix in (("电光叶蝉", "Rd"), ("白背飞虱", "W")):
            rows = [row for row in comparisons if row["treatment"] == treatment]
            self.assertEqual([row["time_label"] for row in rows], ["12", "24", "3d"])
            self.assertEqual([row["time_order"] for row in rows], [1, 2, 3])
            self.assertTrue(all(row["n_treatment"] == 4 and row["n_control"] == 4 for row in rows))
            samples = [
                row
                for row in result["samples"]
                if row["dataset_id"] == LEAFHOPPER_DATASET
                and str(row["original_sample_code"]).startswith(prefix)
            ]
            self.assertEqual(len(samples), 12)
            self.assertTrue(all(row["replicate"] in (1, 2, 3, 4) for row in samples))

    def test_absent_categories_and_plot_exports(self) -> None:
        result = query_lab_omics(["LOC_Os03g28330", "LOC_Os08g31410"], DATABASE)
        statuses = {row["dataset_id"]: row["inclusion_status"] for row in result["status"]}
        self.assertEqual(statuses.get("gray_planthopper_absent"), "absent")
        self.assertEqual(statuses.get("rbsdv_absent"), "absent")
        charts, raw = build_lab_omics_artifacts(result)
        cross = "lab_omics/heatmap_cross_project_log2fc"
        self.assertTrue(charts[f"{cross}.png"].startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(charts[f"{cross}.pdf"].startswith(b"%PDF"))
        self.assertIn(b"<svg", charts[f"{cross}.svg"][:2048])
        self.assertIn(f"{cross}_matrix.csv", raw)
        self.assertIn("lab_omics/query_metadata.json", raw)
        matrices = project_profile_matrices(result)
        leafhopper = next(item for item in matrices if item[0] == LEAFHOPPER_DATASET)
        columns = list(leafhopper[2].columns)
        self.assertEqual(columns[:4], ["NPB_L_1", "NPB_L_2", "NPB_L_3", "NPB_L_4"])
        self.assertLess(columns.index("Rd12_L_1"), columns.index("Rd24_L_1"))
        self.assertLess(columns.index("Rd24_L_1"), columns.index("Rd3d_L_1"))

    def test_query_performance_targets(self) -> None:
        loci = [
            "LOC_Os03g28330", "LOC_Os09g27660", "LOC_Os10g40934", "LOC_Os06g22919",
            "LOC_Os08g31410", "LOC_Os03g48600", "LOC_Os10g42299", "LOC_Os12g08260",
            "LOC_Os08g39140", "LOC_Os06g49250", "LOC_Os01g01010", "LOC_Os01g01020",
            "LOC_Os01g01030", "LOC_Os01g01040", "LOC_Os01g01050", "LOC_Os01g01060",
            "LOC_Os01g01070", "LOC_Os01g01080", "LOC_Os01g01090", "LOC_Os01g01100",
        ]
        started = time.perf_counter()
        query_lab_omics(loci[:1], DATABASE)
        single_elapsed = time.perf_counter() - started
        started = time.perf_counter()
        query_lab_omics(loci, DATABASE)
        batch_elapsed = time.perf_counter() - started
        self.assertLess(single_elapsed, 2.0)
        self.assertLess(batch_elapsed, 5.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
