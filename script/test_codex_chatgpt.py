#!/usr/bin/env python3
"""Regression tests for ChatGPT-authenticated Codex interpretation."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app_source"))

import codex_chatgpt  # noqa: E402
from codex_chatgpt import (  # noqa: E402
    CODEX_ACCOUNT_MODEL,
    CodexClientError,
    CodexClientStatus,
    CodexInvocationCancelled,
    CodexRunResult,
    PROVIDER_CODEX_CHATGPT,
    detect_codex_client,
    parse_codex_jsonl,
    run_codex_interpretation,
)
from report_interpretation import MODE_LLM, generate_interpretations  # noqa: E402
from rice_gene_core import AnalysisBundle  # noqa: E402


def _response_payload() -> dict[str, object]:
    return {
        "executive_summary": "结构化证据支持优先复核。",
        "multiomics_interpretation": "仅支持处理相关变化，不支持因果。",
        "haplotype_interpretation": "缺少性状关联。",
        "integrated_hypotheses": [
            {
                "hypothesis": "候选基因可能参与处理响应。",
                "support": "mRNA变化。",
                "limitations": "缺少蛋白和遗传证据。",
                "experiment": "独立样本qRT-PCR并开展遗传验证。",
            }
        ],
    }


def _jsonl(payload: dict[str, object] | None = None) -> str:
    return "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "fixture"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "agent_message",
                        "text": json.dumps(payload or _response_payload(), ensure_ascii=False),
                    },
                },
                ensure_ascii=False,
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 20}}),
        ]
    )


class _FakeProcess:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int | None = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.pid = 999999
        self.inputs: list[str | None] = []
        self.terminated = False

    def communicate(self, input: str | None = None, timeout: float | None = None) -> tuple[str, str]:
        self.inputs.append(input)
        return self.stdout, self.stderr

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.terminated = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return int(self.returncode or 0)


class CodexChatGPTTests(unittest.TestCase):
    def fixture(self) -> AnalysisBundle:
        bundle = AnalysisBundle(
            mode="单基因深度分析",
            input_type="RAP/MSU ID",
            inputs=["LOC_Os01g01010"],
        )
        bundle.lab_omics_differential = [
            {
                "msu_locus": "LOC_Os01g01010",
                "dataset_name": "RSV RNA-seq",
                "comparison_name": "RSV vs control",
                "assay": "mRNA",
                "log2fc": 1.2,
                "padj": 0.03,
                "source_file": "/secret/raw.xlsx",
            }
        ]
        return bundle

    def authenticated_status(self, executable: str) -> CodexClientStatus:
        return CodexClientStatus(
            available=True,
            authenticated=True,
            platform="Windows",
            version="codex-cli fixture",
            executable=executable,
            message="ok",
        )

    def test_platform_candidates_include_official_locations(self) -> None:
        mac = codex_chatgpt._candidate_paths(
            system_name="Darwin",
            environ={"HOME": "/Users/test"},
            which=lambda _: None,
        )
        self.assertEqual(str(mac[0]), "/Applications/ChatGPT.app/Contents/Resources/codex")
        windows = codex_chatgpt._candidate_paths(
            system_name="Windows",
            environ={"LOCALAPPDATA": "C:/Users/test/AppData/Local", "USERPROFILE": "C:/Users/test"},
            which=lambda _: None,
        )
        self.assertTrue(str(windows[0]).replace("\\", "/").endswith("Programs/OpenAI/Codex/bin/codex.exe"))

    def test_detection_requires_chatgpt_login_and_safe_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "codex"
            executable.write_text("fixture", encoding="utf-8")

            def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                if command[-1] == "--version":
                    return subprocess.CompletedProcess(command, 0, "codex-cli 1.0", "")
                if command[-1] == "--help" and command[-2] == "exec":
                    flags = " ".join(codex_chatgpt._REQUIRED_EXEC_FLAGS)
                    return subprocess.CompletedProcess(command, 0, flags, "")
                if command[-1] == "--help":
                    flags = " ".join(codex_chatgpt._REQUIRED_GLOBAL_FLAGS)
                    return subprocess.CompletedProcess(command, 0, flags, "")
                return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")

            status = detect_codex_client(executable, system_name="Darwin", environ={}, runner=runner)
            self.assertTrue(status.authenticated)
            self.assertEqual(status.version, "codex-cli 1.0")

            def not_logged_in(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                result = runner(command, **kwargs)
                if command[-2:] == ["login", "status"]:
                    return subprocess.CompletedProcess(command, 1, "Not logged in", "")
                return result

            status = detect_codex_client(executable, system_name="Windows", environ={}, runner=not_logged_in)
            self.assertFalse(status.authenticated)
            self.assertEqual(status.error_code, "not_authenticated")

    def test_detection_continues_to_path_after_broken_embedded_client(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            embedded = Path(temporary) / "embedded-codex"
            path_client = Path(temporary) / "path-codex"
            embedded.write_text("fixture", encoding="utf-8")
            path_client.write_text("fixture", encoding="utf-8")

            def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                if command[0] == str(embedded):
                    return subprocess.CompletedProcess(command, 1, "", "broken")
                if command[-1] == "--version":
                    return subprocess.CompletedProcess(command, 0, "codex-cli path", "")
                if command[-1] == "--help" and command[-2] == "exec":
                    return subprocess.CompletedProcess(command, 0, " ".join(codex_chatgpt._REQUIRED_EXEC_FLAGS), "")
                if command[-1] == "--help":
                    return subprocess.CompletedProcess(command, 0, " ".join(codex_chatgpt._REQUIRED_GLOBAL_FLAGS), "")
                return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")

            with patch("codex_chatgpt._candidate_paths", return_value=[embedded, path_client]):
                status = detect_codex_client(system_name="Darwin", environ={}, runner=runner)
            self.assertTrue(status.authenticated)
            self.assertEqual(status.executable, str(path_client))

    def test_environment_excludes_api_keys_and_unrelated_secrets(self) -> None:
        environment = codex_chatgpt._minimal_environment(
            {
                "HOME": "/Users/test",
                "PATH": "/usr/bin",
                "HTTPS_PROXY": "http://proxy",
                "OPENAI_API_KEY": "secret-api-key",
                "MY_BIO_TOOLS_OMICS_KEY_B64": "secret-omics-key",
            }
        )
        self.assertEqual(environment["HOME"], "/Users/test")
        self.assertNotIn("OPENAI_API_KEY", environment)
        self.assertNotIn("MY_BIO_TOOLS_OMICS_KEY_B64", environment)

    def test_jsonl_parser_uses_final_agent_message(self) -> None:
        parsed = json.loads(parse_codex_jsonl(_jsonl()))
        self.assertIn("executive_summary", parsed)
        with self.assertRaises(CodexClientError) as context:
            parse_codex_jsonl(json.dumps({"type": "item.completed", "item": {"type": "error", "message": "usage limit exceeded"}}))
        self.assertEqual(context.exception.code, "usage_limit")

    def test_safe_command_returns_strict_payload_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "codex.exe"
            executable.write_text("fixture", encoding="utf-8")
            process = _FakeProcess(stdout=_jsonl())
            captured: dict[str, object] = {}

            def popen(command: list[str], **kwargs: object) -> _FakeProcess:
                captured["command"] = command
                captured["kwargs"] = kwargs
                return process

            result = run_codex_interpretation(
                "fixture prompt",
                status=self.authenticated_status(str(executable)),
                system_name="Windows",
                environ={"USERPROFILE": temporary, "PATH": temporary},
                popen_factory=popen,
            )
            command = list(captured["command"])
            self.assertIn("--ephemeral", command)
            self.assertIn("--output-schema", command)
            self.assertIn("shell_tool", command)
            self.assertNotIn("fixture prompt", " ".join(command))
            self.assertEqual(process.inputs, ["fixture prompt"])
            self.assertEqual(result.payload["executive_summary"], "结构化证据支持优先复核。")
            self.assertIn("creationflags", dict(captured["kwargs"]))

    def test_cancel_and_process_errors_are_standardized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            executable = Path(temporary) / "codex.exe"
            executable.write_text("fixture", encoding="utf-8")
            cancelled = _FakeProcess(returncode=None)
            with self.assertRaises(CodexInvocationCancelled):
                run_codex_interpretation(
                    "fixture",
                    status=self.authenticated_status(str(executable)),
                    system_name="Windows",
                    environ={"USERPROFILE": temporary},
                    is_cancelled=lambda: True,
                    popen_factory=lambda *_args, **_kwargs: cancelled,
                )
            self.assertTrue(cancelled.terminated)

            limited = _FakeProcess(stderr="rate limit exceeded", returncode=1)
            with self.assertRaises(CodexClientError) as context:
                run_codex_interpretation(
                    "fixture",
                    status=self.authenticated_status(str(executable)),
                    system_name="Windows",
                    environ={"USERPROFILE": temporary},
                    popen_factory=lambda *_args, **_kwargs: limited,
                )
            self.assertEqual(context.exception.code, "usage_limit")

    def test_report_integration_records_provider_and_redacts_payload(self) -> None:
        captured: list[str] = []
        bundle = self.fixture()
        bundle.inputs = ["private_sample_name"]
        bundle.lab_omics_differential[0].update(
            {
                "dataset_name": "private_dataset_alpha",
                "comparison_name": "private_sample_A_vs_private_sample_B",
                "treatment": "private_treatment_label",
            }
        )
        bundle.haplotypes = [
            {
                "input_id": "private_sample_name",
                "haplotype": "H1",
                "sample_count": 2,
                "sample_frequency": 1.0,
                "subgroup_frequency": "private_group:2/2",
                "filtered_variant_count": 1,
            }
        ]

        def fake_run(prompt: str, **_: object) -> CodexRunResult:
            captured.append(prompt)
            return CodexRunResult(_response_payload(), "codex-cli fixture")

        with patch("report_interpretation.codex_chatgpt.run_codex_interpretation", side_effect=fake_run):
            rows, status = generate_interpretations(
                bundle,
                mode=MODE_LLM,
                provider=PROVIDER_CODEX_CHATGPT,
                model=CODEX_ACCOUNT_MODEL,
            )
        self.assertEqual(status["effective_mode"], MODE_LLM)
        self.assertEqual(status["provider"], PROVIDER_CODEX_CHATGPT)
        self.assertEqual(status["client_version"], "codex-cli fixture")
        self.assertTrue(any(row["title"].startswith("AI候选假设") for row in rows))
        self.assertNotIn("/secret/raw.xlsx", captured[0])
        self.assertNotIn("private_sample_name", captured[0])
        self.assertNotIn("private_dataset_alpha", captured[0])
        self.assertNotIn("private_sample_A_vs_private_sample_B", captured[0])
        self.assertNotIn("private_treatment_label", captured[0])
        self.assertNotIn("private_group", captured[0])
        self.assertIn('"analysis_object":["input_1"]', captured[0])
        self.assertIn("dataset_1", captured[0])
        self.assertIn("comparison_1", captured[0])
        self.assertIn("subgroup_1", captured[0])

    def test_codex_failure_falls_back_without_api(self) -> None:
        error = CodexClientError("usage_limit", "额度不足")
        with patch("report_interpretation.codex_chatgpt.run_codex_interpretation", side_effect=error):
            rows, status = generate_interpretations(
                self.fixture(), mode=MODE_LLM, provider=PROVIDER_CODEX_CHATGPT,
            )
        self.assertEqual(status["effective_mode"], "rules")
        self.assertEqual(status["error_code"], "usage_limit")
        self.assertEqual(status["error"], "额度不足")
        self.assertFalse(any(str(row["section"]).startswith("ai_") for row in rows))


if __name__ == "__main__":
    unittest.main()
