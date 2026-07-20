#!/usr/bin/env python3
"""Regression checks for v1.9.8 layered omics display and Word reports."""

from __future__ import annotations

import io
import math
import os
from pathlib import Path
import sqlite3
import sys
import unittest
import zipfile

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = ROOT / "app_source"
if str(APP_SOURCE) not in sys.path:
    sys.path.insert(0, str(APP_SOURCE))

from lab_omics import build_lab_omics_artifacts, cross_project_matrix, query_lab_omics  # noqa: E402
from report_builder import build_ai_interpretation_word_report, build_word_report  # noqa: E402
from report_interpretation import _llm_prompt  # noqa: E402
from rice_gene_core import AnalysisBundle  # noqa: E402


DATABASE = Path(
    os.environ.get("MY_BIO_TOOLS_OMICS_DB")
    or os.environ.get("MY_BIO_TOOLS_TEST_DATABASE")
    or "/Volumes/FAFU/analysis_results/wulab_omics_app_v1/wulab_omics_v1.sqlite"
).expanduser()
TARGET = "LOC_Os08g39890"


def _document_xml(payload: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return archive.read("word/document.xml").decode("utf-8")


def _bundle(result: dict[str, object]) -> AnalysisBundle:
    bundle = AnalysisBundle(mode="单基因深度分析", input_type="MSU locus", inputs=[TARGET])
    bundle.generated_at = "2026-07-20T20:00:00+08:00"
    bundle.lab_omics_datasets = list(result["datasets"])
    bundle.lab_omics_comparisons = list(result["comparisons"])
    bundle.lab_omics_samples = list(result["samples"])
    bundle.lab_omics_differential = list(result["differential"])
    bundle.lab_omics_profiles = list(result["profiles"])
    bundle.lab_omics_status = list(result["status"])
    bundle.lab_omics_published_evidence = list(result["published_evidence"])
    bundle.lab_omics_consensus_scores = list(result["consensus_scores"])
    bundle.lab_omics_qc_metrics = list(result["qc_metrics"])
    bundle.lab_omics_dataset_context = list(result["dataset_context"])
    bundle.lab_omics_dataset_registry = list(result["dataset_registry"])
    bundle.lab_omics_dataset_summaries = list(result["dataset_summaries"])
    bundle.interpretation_status = {
        "requested_mode": "llm",
        "effective_mode": "rules",
        "ai_report_mode": "evidence_fallback",
        "model": "validation-fixture",
    }
    bundle.ai_synthesis = {
        "report_mode": "evidence_fallback",
        "executive_summary": "现有材料可用于核对基因身份和多组学响应范围，但不足以建立新的因果机制。",
        "gene_identity": {"summary": f"分析对象为 {TARGET}。", "evidence_ids": []},
        "core_function": {"summary": "未在验证夹具中补写功能结论。", "evidence_ids": []},
        "mechanism_chains": [],
        "context_branches": [],
        "omics_integration": [],
        "testable_hypotheses": [],
        "knowledge_gaps": ["需要对关键文献和因果实验进行独立核验。"],
        "references": [],
    }
    return bundle


class ReportClarityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not DATABASE.is_file():
            raise unittest.SkipTest(f"database unavailable: {DATABASE}")
        cls.result = query_lab_omics([TARGET], DATABASE)
        cls.charts, cls.raw = build_lab_omics_artifacts(cls.result)

    def test_database_and_target_coverage(self) -> None:
        with sqlite3.connect(DATABASE) as connection:
            primary_count = connection.execute(
                "SELECT COUNT(*) FROM datasets WHERE search_section='primary' AND inclusion_status='included' AND biological_replicates_verified=1"
            ).fetchone()[0]
        self.assertEqual(primary_count, 13)
        primary = [row for row in self.result["dataset_summaries"] if row["display_tier"] != "published_evidence"]
        self.assertEqual(len(primary), 11)
        self.assertEqual(sum(row["display_tier"] == "differential" for row in primary), 5)
        self.assertEqual(sum(row["display_tier"] == "abundance_only" for row in primary), 6)
        project_keys = {
            str(row.get("accession") or row.get("dataset_name")).replace("_9311", "").replace("_Nipponbare", "")
            for row in primary
        }
        self.assertEqual(len(project_keys), 10, "PRJNA735790 contains two biological backgrounds but one source project")

    def test_short_labels_and_heatmap_missingness(self) -> None:
        primary = [row for row in self.result["dataset_summaries"] if row["display_tier"] != "published_evidence"]
        labels = [str(row["short_label"]) for row in primary]
        self.assertEqual(len(labels), len(set(labels)))
        for row, label in zip(primary, labels):
            self.assertIn("\n", label)
            self.assertNotIn(str(row["dataset_id"]), label)
            self.assertNotIn("pub_20260720", label)
            self.assertLessEqual(max(map(len, label.splitlines())), 64)
        matrix, long = cross_project_matrix(self.result)
        self.assertEqual(matrix.shape, (1, 11))
        self.assertEqual(int(matrix.notna().sum(axis=1).iloc[0]), 5)
        self.assertEqual(int(matrix.isna().sum(axis=1).iloc[0]), 6)
        self.assertEqual(set(long["annotation"]), {"NA", "-0.11", "-1.05", "1.03", "-0.03", "0.38"})

    def test_artifact_formats_and_plotting_data(self) -> None:
        expected_stems = ["heatmap_cross_project_log2fc", "overview_LOC_Os08g39890_virus_response", "overview_LOC_Os08g39890_insect_response"]
        for stem in expected_stems:
            for extension in ("svg", "pdf", "png"):
                self.assertIn(f"lab_omics/{stem}.{extension}", self.charts)
        primary = [row for row in self.result["dataset_summaries"] if row["display_tier"] != "published_evidence"]
        for row in primary:
            stem = f"response_{TARGET}_{row['dataset_id']}"
            for extension in ("svg", "pdf", "png"):
                self.assertIn(f"lab_omics/{stem}.{extension}", self.charts)
            self.assertIn(f"lab_omics/{stem}.csv", self.raw)
        image = Image.open(io.BytesIO(self.charts["lab_omics/heatmap_cross_project_log2fc.png"]))
        dpi = image.info.get("dpi", (0, 0))[0]
        self.assertTrue(math.isclose(float(dpi), 600.0, rel_tol=0.01))
        self.assertIn("lab_omics/dataset_coverage_summary.csv", self.raw)

    def test_prompt_is_scientific_review_style(self) -> None:
        bundle = _bundle(self.result)
        system, _ = _llm_prompt(bundle, [])
        for phrase in ("中文科研综述体", "每段第一句", "不得自行生成", "evidence_id", "testable_hypotheses"):
            self.assertIn(phrase, system)
        for banned in ("值得注意的是", "综上所述"):
            self.assertIn(banned, system)

    def test_word_reports_include_full_layered_coverage(self) -> None:
        bundle = _bundle(self.result)
        standard = build_word_report(bundle, include_full_sequences=False, deep_charts=self.charts)
        deep = build_ai_interpretation_word_report(bundle, deep_charts=self.charts)
        standard_xml = _document_xml(standard)
        deep_xml = _document_xml(deep)
        for row in self.result["dataset_summaries"]:
            self.assertIn(str(row["dataset_name"]), standard_xml)
        self.assertIn("差异统计", standard_xml)
        self.assertIn("仅定量观察", standard_xml)
        self.assertNotIn("关键结论", standard_xml)
        self.assertIn("功能与机制证据整理报告", deep_xml)
        self.assertIn("模型综合未完成", deep_xml)
        self.assertGreaterEqual(deep_xml.count('w:type="page"'), 5)
        self.assertIn("wp:docPr", standard_xml)
        self.assertIn("wp:docPr", deep_xml)


if __name__ == "__main__":
    unittest.main(verbosity=2)
