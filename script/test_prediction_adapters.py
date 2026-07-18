#!/usr/bin/env python3
"""Mocked contract tests for all five official web prediction adapters."""

from __future__ import annotations

from pathlib import Path
import os
import sys
import unittest
from unittest.mock import patch

import requests


ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = ROOT / "app_source"
sys.path.insert(0, str(APP_SOURCE))

import prediction_services as predictors  # noqa: E402


PROTEIN = "MKRKRTKQKRRKAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def response(text: str, url: str) -> requests.Response:
    item = requests.Response()
    item.status_code = 200
    item.url = url
    item._content = text.encode("utf-8")
    item.encoding = "utf-8"
    return item


class FakeSession:
    def __init__(self, post_response: requests.Response, get_responses=None):
        self.post_response = post_response
        self.get_responses = list(get_responses or [])
        self.posts: list[tuple[str, dict[str, object]]] = []
        self.gets: list[str] = []

    def post(self, url: str, data=None, files=None, **kwargs):
        payload = dict(data or {})
        payload.update({key: value[1] for key, value in (files or {}).items()})
        self.posts.append((url, payload))
        return self.post_response

    def get(self, url: str, **kwargs):
        self.gets.append(url)
        if self.get_responses:
            return self.get_responses.pop(0)
        return self.post_response


class FakeBioLibFile:
    def __init__(self, path: str, payload: bytes):
        self.path = path
        self.payload = payload

    def get_data(self):
        return self.payload


class FakeBioLibJob:
    def __init__(self, outputs: dict[str, bytes], finished: bool = True):
        self.id = "fake-biolib-job"
        self.outputs = outputs
        self.finished = finished

    def is_finished(self):
        return self.finished

    def get_exit_code(self):
        return 0

    def get_stdout(self):
        return b""

    def list_output_files(self):
        return [FakeBioLibFile(path, payload) for path, payload in self.outputs.items()]

    def get_output_file(self, name):
        return FakeBioLibFile(name, self.outputs[name])


class FakeBioLibApp:
    def __init__(self, job: FakeBioLibJob):
        self.job = job
        self.args = ""

    def cli(self, args=None, **kwargs):
        self.args = str(args or "")
        return self.job


class FakeBioLibModule:
    def __init__(self, app: FakeBioLibApp):
        self.app = app

    def load(self, uri):
        return self.app


class PredictionAdapterTests(unittest.TestCase):
    def test_biolib_token_is_redacted_from_errors(self) -> None:
        with patch.dict(os.environ, {"BIOLIB_TOKEN": "secret-token-value"}):
            message = predictors._safe_error("Authorization: Bearer secret-token-value")
        self.assertNotIn("secret-token-value", message)
        self.assertIn("[REDACTED]", message)

    def test_all_four_dtu_adapters_parse_success(self) -> None:
        texts = {
            "SignalP 6.0": "query Sec/SPI signal peptide: 1-20",
            "TMHMM 2.0": "Number of predicted TMHs: 1\nquery TMhelix 8 30",
            "DeepTMHMM 1.0": "query TM 8-30 GLOB",
            "TargetP 2.0": "query cTP 0.91",
        }
        for tool, text in texts.items():
            with self.subTest(tool=tool):
                session = FakeSession(response(text, f"https://services.healthtech.dtu.dk/job/{tool}"))
                with patch.object(predictors, "create_session", return_value=session):
                    result = predictors.run_dtu_prediction(tool, "query", PROTEIN, timeout_seconds=2)
                self.assertEqual(result.status, "matched")
                self.assertTrue(result.classification)
                self.assertIn("configfile", session.posts[0][1])
                if tool in {"SignalP 6.0", "TargetP 2.0"}:
                    self.assertEqual(session.posts[0][1]["format"], "long")

    def test_biolib_fallback_parses_batch_outputs_and_keeps_attempts(self) -> None:
        failed = predictors.PredictionExecution(
            results=[
                predictors.PredictionResult(
                    protein_id="query",
                    tool="SignalP 6.0",
                    version="6.0",
                    status="failed",
                    error="DTU failed",
                    attempts=[
                        predictors.PredictionProviderAttempt(
                            "dtu_web", "failed", "DTU123", "https://example/dtu", "DTU failed"
                        )
                    ],
                )
            ]
        )
        succeeded = predictors.PredictionExecution(
            results=[
                predictors.PredictionResult(
                    protein_id="query",
                    tool="SignalP 6.0",
                    version="6.0",
                    status="matched",
                    classification="Sec/SPI",
                    provider="biolib",
                    provider_job_id="BIO123",
                    fallback_used=True,
                    probabilities={"OTHER": 0.02, "SP": 0.98},
                    attempts=[
                        predictors.PredictionProviderAttempt(
                            "biolib", "matched", "BIO123", "https://example/biolib"
                        )
                    ],
                )
            ],
            raw_artifacts={"biolib/result.gff3": b"##gff-version 3\n"},
        )
        with (
            patch.object(predictors, "run_dtu_batch", return_value=failed),
            patch.object(predictors, "run_biolib_batch", return_value=succeeded),
        ):
            execution = predictors.run_resilient_batch("SignalP 6.0", [("query", PROTEIN)])
        result = execution.results[0]
        self.assertEqual(result.provider, "biolib")
        self.assertTrue(result.fallback_used)
        self.assertEqual([attempt.provider for attempt in result.attempts], ["dtu_web", "biolib"])
        self.assertIn("biolib/result.gff3", execution.raw_artifacts)

    def test_biolib_batch_preserves_ids_and_parses_probabilities(self) -> None:
        prediction_table = b"""# ID\tPrediction\tOTHER\tSP(Sec/SPI)\tCS Position
protein_A\tSP\t0.01\t0.99\tCS pos: 20-21. Pr: 0.95
protein_B\tOTHER\t0.98\t0.02\t
"""
        gff = b"""## gff-version 3
protein_A\tSignalP-6.0\tsignal_peptide\t1\t20\t0.99\t.\t.\t.
"""
        job = FakeBioLibJob({"prediction_results.txt": prediction_table, "output.gff3": gff})
        app = FakeBioLibApp(job)
        with patch.object(predictors, "biolib", FakeBioLibModule(app)):
            execution = predictors.run_biolib_batch(
                "SignalP 6.0",
                [("protein_A", PROTEIN), ("protein_B", PROTEIN)],
                timeout_seconds=2,
            )
        self.assertEqual([result.protein_id for result in execution.results], ["protein_A", "protein_B"])
        self.assertEqual(execution.results[0].classification, "Sec/SPI")
        self.assertAlmostEqual(execution.results[0].probabilities["SP(Sec/SPI)"], 0.99)
        self.assertEqual(execution.results[1].classification, "OTHER")
        self.assertIn("--format all", app.args)

    def test_biolib_cancel_and_timeout_are_isolated(self) -> None:
        job = FakeBioLibJob({}, finished=False)
        with patch.object(predictors, "biolib", FakeBioLibModule(FakeBioLibApp(job))):
            cancelled = predictors.run_biolib_batch(
                "DeepTMHMM 1.0", [("query", PROTEIN)], timeout_seconds=2, cancel_check=lambda: True
            )
        self.assertEqual(cancelled.results[0].status, "cancelled")
        with patch.object(predictors, "biolib", FakeBioLibModule(FakeBioLibApp(job))):
            timed_out = predictors.run_biolib_batch(
                "DeepTMHMM 1.0", [("query", PROTEIN)], timeout_seconds=0
            )
        self.assertEqual(timed_out.results[0].status, "failed")
        self.assertIn("超过 0 秒", timed_out.results[0].error)

    def test_deeptmhmm_three_line_topology_parser(self) -> None:
        classes, regions = predictors._parse_deep_topology(
            ">query | predicted topology\nMKKLLLLAAAA\nSSSMMMMMOOO\n",
            {"query": "MKKLLLLAAAA"},
        )
        self.assertIn("signal peptide", classes["query"])
        self.assertEqual(
            [(item.region_type, item.start, item.end) for item in regions["query"]],
            [("signal peptide", 1, 3), ("TMhelix", 4, 8), ("outside", 9, 11)],
        )

    def test_signalp_prediction_table_is_not_overwritten_by_gff(self) -> None:
        parsed = predictors._parse_probability_table(
            """SOURCE prediction_results.txt
# SignalP-6.0\tOrganism: Eukarya
# ID\tPrediction\tOTHER\tSP(Sec/SPI)\tCS Position
CLV3_ARATH\tSP\t0.000248\t0.999713\tCS pos: 21-22. Pr: 0.9671
SOURCE output.gff3
## gff-version 3
CLV3_ARATH\tSignalP-6.0\tsignal_peptide\t1\t21\t0.9997\t.\t.\t.
"""
        )
        self.assertEqual(parsed["CLV3_ARATH"]["classification"], "SP")
        self.assertEqual(parsed["CLV3_ARATH"]["cleavage_site"], 21)
        self.assertAlmostEqual(parsed["CLV3_ARATH"]["probabilities"]["SP(Sec/SPI)"], 0.999713)

    def test_dtu_queue_then_success(self) -> None:
        queued = response(
            '<meta http-equiv="refresh" content="0; url=/job/queued">queued please wait',
            predictors.DTU_SUBMIT_URL,
        )
        done = response("query OTHER", "https://services.healthtech.dtu.dk/job/queued")
        session = FakeSession(queued, [done])
        with (
            patch.object(predictors, "create_session", return_value=session),
            patch.object(predictors.time, "sleep", return_value=None),
        ):
            result = predictors.run_dtu_prediction("SignalP 6.0", "query", PROTEIN, timeout_seconds=2)
        self.assertEqual(result.status, "matched")
        self.assertEqual(len(session.gets), 1)

    def test_dtu_rejection_timeout_and_structure_change_are_isolated(self) -> None:
        rejected = FakeSession(response("Job failed: invalid sequence", predictors.DTU_SUBMIT_URL))
        with patch.object(predictors, "create_session", return_value=rejected):
            result = predictors.run_dtu_prediction("TargetP 2.0", "query", PROTEIN, timeout_seconds=2)
        self.assertEqual(result.status, "failed")

        queued = FakeSession(response("queued please wait", predictors.DTU_SUBMIT_URL))
        with patch.object(predictors, "create_session", return_value=queued):
            result = predictors.run_dtu_prediction("DeepTMHMM 1.0", "query", PROTEIN, timeout_seconds=0)
        self.assertEqual(result.status, "timeout")
        self.assertIn(">query", result.raw_text)

        changed = FakeSession(response("unexpected redesigned page", predictors.DTU_SUBMIT_URL))
        with patch.object(predictors, "create_session", return_value=changed):
            result = predictors.run_dtu_prediction("TMHMM 2.0", "query", PROTEIN, timeout_seconds=0)
        self.assertEqual(result.status, "timeout")
        self.assertIn("Manual submission FASTA", result.raw_text)

    def test_cnls_mapper_success_and_failure_keep_trace(self) -> None:
        success = FakeSession(
            response(
                """<table><tr><th colspan='3'>Predicted monopartite NLS</th></tr>
                <tr><th>Pos.</th><th>Sequence</th><th>Score</th></tr>
                <tr><td>2</td><td>KRKRTKQKRRKAAA</td><td>8.5</td></tr></table>""",
                "https://nls-mapper.iab.keio.ac.jp/cgi-bin/NLS_Mapper_y.cgi",
            )
        )
        with patch.object(predictors, "create_session", return_value=success):
            result = predictors.run_cnls_mapper("query", PROTEIN, cutoff=5.0)
        self.assertEqual(result.status, "matched")
        self.assertEqual(result.classification, "cNLS detected")
        self.assertEqual((result.regions[0].start, result.regions[0].end), (2, 15))
        self.assertEqual(result.regions[0].score, 8.5)

        class FailedSession(FakeSession):
            def post(self, *args, **kwargs):
                raise requests.ConnectionError("mock network failure")

        with patch.object(
            predictors,
            "create_session",
            return_value=FailedSession(response("", predictors.TOOL_URLS["cNLS Mapper"])),
        ):
            result = predictors.run_cnls_mapper("query", PROTEIN)
        self.assertEqual(result.status, "failed")
        self.assertIn(">query", result.raw_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
