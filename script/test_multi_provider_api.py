#!/usr/bin/env python3
"""Offline contract tests for the multi-provider, session-key-only routes."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

import requests


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app_source"))

from llm_providers import (  # noqa: E402
    CLOUD_API_PROVIDERS,
    CLOUD_PROVIDER_PRESETS,
    chat_completions_url,
    cloud_preference_keys,
)
from model_preferences import (  # noqa: E402
    DEFAULT_INTERPRETATION_PREFERENCES,
    MODE_LLM,
    load_interpretation_preferences,
    save_interpretation_preferences,
    start_model_connection_test,
)
from report_interpretation import _post_cloud_chat, probe_model_connection  # noqa: E402


class FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def post(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        if not self.responses:
            raise AssertionError("Unexpected extra HTTP request")
        return self.responses.pop(0)


class MultiProviderApiTests(unittest.TestCase):
    def test_registry_is_complete_and_non_secret(self) -> None:
        self.assertEqual(len(CLOUD_API_PROVIDERS), 6)
        self.assertEqual(len(CLOUD_PROVIDER_PRESETS), 6)
        self.assertEqual(
            {item.provider_id for item in CLOUD_PROVIDER_PRESETS},
            set(CLOUD_API_PROVIDERS),
        )
        for preset in CLOUD_PROVIDER_PRESETS:
            self.assertTrue(preset.default_base_url.startswith("https://"))
            self.assertTrue(preset.api_docs_url.startswith("https://"))
            serialized = repr(preset).casefold()
            self.assertNotIn("api_key", serialized)
            self.assertNotIn("token", serialized)

    def test_preference_file_drops_api_keys_and_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            target = Path(temporary_dir) / "preferences.json"
            source: dict[str, object] = dict(DEFAULT_INTERPRETATION_PREFERENCES)
            source.update(
                {
                    "mode": MODE_LLM,
                    "provider": "deepseek_api",
                    "deepseek_model": "test-model",
                    "api_key": "must-not-be-written",
                    "deepseek_api_key": "must-not-be-written-either",
                }
            )
            saved = save_interpretation_preferences(source, path=target)
            serialized = target.read_text(encoding="utf-8")
            self.assertNotIn("must-not-be-written", serialized)
            self.assertNotIn("api_key", serialized)
            self.assertEqual(saved["deepseek_model"], "test-model")
            self.assertEqual(load_interpretation_preferences(path=target), saved)

    def test_startup_requires_key_but_does_not_persist_it(self) -> None:
        preferences = dict(DEFAULT_INTERPRETATION_PREFERENCES)
        preferences.update({"mode": MODE_LLM, "provider": "qwen_dashscope_api"})
        result = start_model_connection_test(preferences, background=False)
        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "needs_api_key")

    def test_each_cloud_provider_uses_compatible_chat_endpoint(self) -> None:
        for preset in CLOUD_PROVIDER_PRESETS:
            with self.subTest(provider=preset.provider_id):
                session = FakeSession(
                    [FakeResponse({"choices": [{"message": {"content": "OK"}}]})]
                )
                detail = probe_model_connection(
                    provider=preset.provider_id,
                    base_url=preset.default_base_url,
                    model=preset.default_model or "test-model",
                    api_key="session-only-key",
                    session=session,
                )
                self.assertIn(preset.label, detail)
                self.assertEqual(
                    session.calls[0]["url"],
                    chat_completions_url(preset.default_base_url),
                )
                headers = session.calls[0]["headers"]
                self.assertEqual(headers["Authorization"], "Bearer session-only-key")
                self.assertEqual(session.calls[0]["json"]["messages"][0]["content"], "Reply with OK.")

    def test_json_mode_retries_without_response_format_when_rejected(self) -> None:
        session = FakeSession(
            [
                FakeResponse({"error": "unsupported"}, status_code=400),
                FakeResponse({"choices": [{"message": {"content": "{}"}}]}),
            ]
        )
        response = _post_cloud_chat(
            session,
            provider="deepseek_api",
            base_url="https://example.invalid/v1",
            api_key="session-only-key",
            payload={
                "model": "test-model",
                "messages": [],
                "response_format": {"type": "json_object"},
            },
            timeout=1,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(session.calls), 2)
        self.assertIn("response_format", session.calls[0]["json"])
        self.assertNotIn("response_format", session.calls[1]["json"])

    def test_every_provider_has_separate_non_secret_preference_keys(self) -> None:
        keys = [cloud_preference_keys(item.provider_id) for item in CLOUD_PROVIDER_PRESETS]
        flattened = [value for pair in keys for value in pair]
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertFalse(any("key" in value.casefold() for value in flattened))


if __name__ == "__main__":
    unittest.main(verbosity=2)
