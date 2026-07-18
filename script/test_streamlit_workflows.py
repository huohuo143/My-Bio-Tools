#!/usr/bin/env python3
"""End-to-end Streamlit workflows for tools that do not require file upload."""

from __future__ import annotations

import os
from pathlib import Path
import random
import sys
import threading
import time
import unittest

os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = ROOT / "app_source"
MAIN_SCRIPT = APP_SOURCE / "main.py"
sys.path.insert(0, str(APP_SOURCE))

from analysis_jobs import JOB_MANAGER, RiceGeneAnalysisRequest  # noqa: E402
from rice_gene_core import AnalysisBundle  # noqa: E402
from rice_utr_promoter_downloader import TRANSCRIPT_SCOPE_ALL  # noqa: E402


class StreamlitWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.chdir(APP_SOURCE)
        sys.path.insert(0, str(APP_SOURCE))

    def open_tool(self, category: str, tool: str) -> AppTest:
        app = AppTest.from_file(str(MAIN_SCRIPT), default_timeout=30)
        app.run()
        app.sidebar.radio[0].set_value(category).run()
        app.sidebar.selectbox[0].set_value(tool).run()
        self.assertFalse(app.exception)
        return app

    def assert_no_runtime_errors(self, app: AppTest) -> None:
        self.assertFalse(app.exception)
        self.assertFalse(app.error, [element.value for element in app.error])

    def test_sequence_composition_workflow(self) -> None:
        app = self.open_tool("生信小工具", "DNA 组成与质量检查")
        app.text_area[0].set_value(">seq1\nACGTNN\n>seq2\nGGCC\n").run()
        app.button[0].click().run()
        self.assert_no_runtime_errors(app)
        metrics = {item.label: item.value for item in app.metric}
        self.assertEqual(metrics["序列数"], "2")
        self.assertEqual(metrics["总长度"], "10 nt")
        self.assertEqual(len(app.dataframe), 1)

    def test_rap_msu_conversion_workflow(self) -> None:
        app = self.open_tool("生信小工具", "RAP ↔ MSU ID 转换")
        app.text_area[0].set_value("Os01g0100100\nLOC_Os01g01019").run()
        app.button[0].click().run()
        self.assert_no_runtime_errors(app)
        metrics = {item.label: item.value for item in app.metric}
        self.assertEqual(metrics["成功映射"], "2")
        result = app.dataframe[0].value
        self.assertEqual(result.loc[0, "mapping_count"], 2)
        self.assertEqual(result.loc[1, "converted"], "Os01g0100200")

    def test_primer3_design_workflow(self) -> None:
        random.seed(42)
        sequence = "".join(random.choice("ACGT") for _ in range(700))
        app = self.open_tool("生信小工具", "Primer3 引物设计")
        app.text_area[0].set_value(sequence).run()
        app.button[0].click().run()
        self.assert_no_runtime_errors(app)
        self.assertTrue(app.success)
        self.assertEqual(len(app.dataframe), 1)
        self.assertGreaterEqual(len(app.dataframe[0].value), 1)

    def test_unified_rice_gene_page_defaults(self) -> None:
        app = self.open_tool("水稻资源", "水稻基因一站式分析")
        self.assert_no_runtime_errors(app)
        self.assertEqual(app.radio[0].value, "单基因深度分析")
        self.assertEqual(app.radio[1].value, "RAP/MSU ID")
        self.assertEqual(len(app.multiselect[0].value), 6)
        self.assertEqual(len(app.multiselect[1].value), 6)
        self.assertEqual(app.multiselect[2].value, ["protein_domains", "gene_structure", "promoter_regulation", "literature_evidence"])
        self.assertEqual(app.multiselect[3].value, ["rice_rma", "ricestress_rma"])
        self.assertFalse(app.checkbox[0].value)
        self.assertTrue(app.checkbox[2].value)
        self.assertTrue(app.checkbox[3].value)
        page_text = "\n".join(
            str(getattr(element, "value", getattr(element, "label", "")))
            for element in (
                app.get("text")
                + app.get("markdown")
                + app.get("caption")
                + app.get("warning")
                + app.get("expander")
            )
        )
        self.assertIn("Absolute 不是 fold change", page_text)
        self.assertIn("12 个数据源", page_text)

    def test_background_job_continues_across_tool_navigation(self) -> None:
        gate = threading.Event()
        request = RiceGeneAnalysisRequest(
            project_name="navigation background test",
            mode="单基因深度分析",
            input_type="RAP/MSU ID",
            text="Os01g0100100",
            selected_types=("CDS",),
            promoter_length=2000,
            transcript_scope=TRANSCRIPT_SCOPE_ALL,
            selected_predictors=(),
            signalp_mode="fast",
            cnls_cutoff=5.0,
            nlstradamus_model=1,
            nlstradamus_cutoff=0.6,
            max_workers=1,
            include_ricedata=False,
            include_efp=False,
        )

        def runner(job_request, reporter):
            reporter.complete("mapping", "mapped")
            while not gate.wait(0.01):
                reporter.check_cancel()
            reporter.complete("sequences", "sequence complete")
            reporter.complete("report", "report complete")
            return AnalysisBundle(
                mode=job_request.mode,
                input_type=job_request.input_type,
                inputs=[job_request.text],
            ), {"stem": "test", "docx": b"", "xlsx": b"", "zip": b"", "efp_charts": {}}

        job_id = JOB_MANAGER.submit(request, runner)
        try:
            app = self.open_tool("水稻资源", "水稻基因一站式分析")
            self.assert_no_runtime_errors(app)
            page_running = next(item for item in JOB_MANAGER.snapshots() if item.job_id == job_id)
            self.assertEqual(len(app.get("progress")), len(page_running.progress_items))
            app.sidebar.radio[0].set_value("生信小工具").run()
            app.sidebar.selectbox[0].set_value("DNA 组成与质量检查").run()
            self.assert_no_runtime_errors(app)
            running = next(item for item in JOB_MANAGER.snapshots() if item.job_id == job_id)
            self.assertEqual(running.status, "running")
            self.assertEqual(len(app.get("progress")), 0)
            gate.set()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                finished = next(item for item in JOB_MANAGER.snapshots() if item.job_id == job_id)
                if finished.status == "completed":
                    break
                time.sleep(0.01)
            self.assertEqual(finished.status, "completed")
        finally:
            gate.set()


if __name__ == "__main__":
    unittest.main(verbosity=2)
