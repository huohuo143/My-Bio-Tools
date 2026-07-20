"""Persistent, non-secret model preferences and startup connection checks.

The Streamlit session disappears when the desktop app exits.  This module keeps
only the user's non-sensitive model choices in the per-user application support
directory and owns a process-local connection-test registry.  Passwords, API
keys, tokens and research content must never be written here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import tempfile
import threading
from typing import Mapping

from llm_providers import (
    CLOUD_API_PROVIDERS,
    CLOUD_PROVIDER_PRESETS,
    PROVIDER_CHATANYWHERE,
    PROVIDER_DEEPSEEK,
    PROVIDER_DOUBAO,
    PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_QWEN,
    PROVIDER_ZHIPU,
    cloud_preference_keys,
)


MODE_RULES = "rules"
MODE_LLM = "llm"
PROVIDER_CODEX_CHATGPT = "codex_chatgpt"
PROVIDER_OLLAMA = "ollama"

CODEX_ACCOUNT_MODEL = "account_default"
CODEX_DEFAULT_REASONING = "model_default"
CODEX_DEFAULT_SPEED = "standard"

PREFERENCE_SCHEMA_VERSION = 2
CONFIG_DIR_ENV = "MY_BIO_TOOLS_CONFIG_DIR"
PREFERENCES_FILENAME = "preferences.json"

DEFAULT_INTERPRETATION_PREFERENCES: dict[str, str] = {
    "mode": MODE_RULES,
    "provider": PROVIDER_CODEX_CHATGPT,
    "codex_model": CODEX_ACCOUNT_MODEL,
    "codex_reasoning": CODEX_DEFAULT_REASONING,
    "codex_speed": CODEX_DEFAULT_SPEED,
    "ollama_base_url": "http://127.0.0.1:11434",
    "ollama_model": "qwen2.5:14b",
}
for _cloud_preset in CLOUD_PROVIDER_PRESETS:
    _base_key, _model_key = cloud_preference_keys(_cloud_preset.provider_id)
    DEFAULT_INTERPRETATION_PREFERENCES[_base_key] = _cloud_preset.default_base_url
    DEFAULT_INTERPRETATION_PREFERENCES[_model_key] = _cloud_preset.default_model

_VALID_MODES = {MODE_RULES, MODE_LLM}
_VALID_PROVIDERS = {
    PROVIDER_CODEX_CHATGPT,
    PROVIDER_OLLAMA,
    *CLOUD_API_PROVIDERS,
}
_MAX_PREFERENCE_TEXT = 2048


def _safe_text(value: object, default: str = "") -> str:
    text = str(value or "").strip()
    return text[:_MAX_PREFERENCE_TEXT] if text else default


def _codex_options() -> tuple[set[str], dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]:
    """Read the live Codex option contract while retaining safe fallbacks."""
    try:
        from codex_chatgpt import (
            CODEX_MODEL_OPTIONS,
            codex_reasoning_options,
            codex_speed_options,
        )

        models = {value for value, _label in CODEX_MODEL_OPTIONS}
        reasoning = {model: tuple(codex_reasoning_options(model)) for model in models}
        speeds = {model: tuple(codex_speed_options(model)) for model in models}
        return models, reasoning, speeds
    except Exception:
        models = {CODEX_ACCOUNT_MODEL}
        return (
            models,
            {CODEX_ACCOUNT_MODEL: (CODEX_DEFAULT_REASONING,)},
            {CODEX_ACCOUNT_MODEL: (CODEX_DEFAULT_SPEED,)},
        )


def normalize_interpretation_preferences(payload: Mapping[str, object] | None) -> dict[str, str]:
    """Return a validated preference dictionary with no unknown or secret keys."""
    source = dict(payload or {})
    if isinstance(source.get("rice_result_interpretation"), Mapping):
        source = dict(source["rice_result_interpretation"])  # type: ignore[arg-type]

    normalized = dict(DEFAULT_INTERPRETATION_PREFERENCES)
    mode = _safe_text(source.get("mode"), normalized["mode"])
    provider = _safe_text(source.get("provider"), normalized["provider"])
    normalized["mode"] = mode if mode in _VALID_MODES else MODE_RULES
    normalized["provider"] = provider if provider in _VALID_PROVIDERS else PROVIDER_CODEX_CHATGPT

    for key in (
        "codex_model",
        "codex_reasoning",
        "codex_speed",
        "ollama_base_url",
        "ollama_model",
    ):
        normalized[key] = _safe_text(source.get(key), normalized[key])
    for preset in CLOUD_PROVIDER_PRESETS:
        base_key, model_key = cloud_preference_keys(preset.provider_id)
        normalized[base_key] = _safe_text(source.get(base_key), normalized[base_key])
        normalized[model_key] = _safe_text(source.get(model_key), normalized[model_key])

    models, reasoning_by_model, speeds_by_model = _codex_options()
    if normalized["codex_model"] not in models:
        normalized["codex_model"] = CODEX_ACCOUNT_MODEL
    model = normalized["codex_model"]
    if normalized["codex_reasoning"] not in reasoning_by_model.get(model, ()):
        normalized["codex_reasoning"] = CODEX_DEFAULT_REASONING
    if normalized["codex_speed"] not in speeds_by_model.get(model, ()):
        normalized["codex_speed"] = CODEX_DEFAULT_SPEED
    return normalized


def preferences_path(
    *,
    environ: Mapping[str, str] | None = None,
    system_name: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return the per-user writable preference path for the current platform."""
    env = environ if environ is not None else os.environ
    override = _safe_text(env.get(CONFIG_DIR_ENV))
    if override:
        return Path(override).expanduser() / PREFERENCES_FILENAME

    current_system = system_name or platform.system()
    user_home = home or Path(env.get("HOME") or env.get("USERPROFILE") or Path.home())
    if current_system == "Darwin":
        root = user_home / "Library" / "Application Support" / "My Bio Tools"
    elif current_system == "Windows":
        local_app_data = _safe_text(env.get("LOCALAPPDATA"))
        root = Path(local_app_data) / "My Bio Tools" if local_app_data else user_home / "AppData" / "Local" / "My Bio Tools"
    else:
        xdg_config = _safe_text(env.get("XDG_CONFIG_HOME"))
        root = Path(xdg_config) / "my-bio-tools" if xdg_config else user_home / ".config" / "my-bio-tools"
    return root / PREFERENCES_FILENAME


def load_interpretation_preferences(*, path: Path | None = None) -> dict[str, str]:
    target = path or preferences_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, OSError, UnicodeError, json.JSONDecodeError):
        return dict(DEFAULT_INTERPRETATION_PREFERENCES)
    if not isinstance(payload, dict):
        return dict(DEFAULT_INTERPRETATION_PREFERENCES)
    return normalize_interpretation_preferences(payload)


def save_interpretation_preferences(
    preferences: Mapping[str, object],
    *,
    path: Path | None = None,
) -> dict[str, str]:
    """Atomically save whitelisted choices; API keys and unknown fields are dropped."""
    normalized = normalize_interpretation_preferences(preferences)
    target = path or preferences_path()
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "schema_version": PREFERENCE_SCHEMA_VERSION,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "rice_result_interpretation": normalized,
    }
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            temporary_path.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary_path, target)
        temporary_path = None
        try:
            target.chmod(0o600)
        except OSError:
            pass
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
    return normalized


def model_connection_fingerprint(
    provider: str,
    base_url: str,
    model: str,
    api_key: str = "",
    *,
    reasoning: str = "",
    speed: str = "",
) -> str:
    key_digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest() if api_key else ""
    raw = "|".join(
        (
            provider.strip(),
            base_url.strip(),
            model.strip(),
            key_digest,
            reasoning.strip(),
            speed.strip(),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def preference_connection_fingerprint(
    preferences: Mapping[str, object],
    *,
    api_key: str = "",
) -> str:
    selected = normalize_interpretation_preferences(preferences)
    provider = selected["provider"]
    if provider == PROVIDER_CODEX_CHATGPT:
        return model_connection_fingerprint(
            provider,
            "chatgpt-account",
            selected["codex_model"],
            reasoning=selected["codex_reasoning"],
            speed=selected["codex_speed"],
        )
    if provider == PROVIDER_OLLAMA:
        base_key, model_key = "ollama_base_url", "ollama_model"
    else:
        base_key, model_key = cloud_preference_keys(provider)
    return model_connection_fingerprint(
        provider,
        selected[base_key],
        selected[model_key],
        api_key,
    )


@dataclass(frozen=True)
class ModelConnectionTestResult:
    fingerprint: str
    status: str
    message: str
    checked_at: str

    def public_dict(self) -> dict[str, str]:
        return asdict(self)


_connection_lock = threading.RLock()
_connection_results: dict[str, ModelConnectionTestResult] = {}


def _result(fingerprint: str, status: str, message: str) -> ModelConnectionTestResult:
    return ModelConnectionTestResult(
        fingerprint=fingerprint,
        status=status,
        message=_safe_text(message, "未返回连接状态。"),
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


def record_model_connection_test_result(
    fingerprint: str,
    *,
    ok: bool,
    message: str,
) -> dict[str, str]:
    item = _result(fingerprint, "ok" if ok else "error", message)
    with _connection_lock:
        _connection_results[fingerprint] = item
    return item.public_dict()


def get_model_connection_test_result(fingerprint: str) -> dict[str, str] | None:
    with _connection_lock:
        item = _connection_results.get(fingerprint)
    return item.public_dict() if item else None


def _probe_connection(preferences: Mapping[str, object], api_key: str) -> str:
    selected = normalize_interpretation_preferences(preferences)
    provider = selected["provider"]
    if provider == PROVIDER_CODEX_CHATGPT:
        from codex_chatgpt import detect_codex_client, probe_codex_connection

        status = detect_codex_client()
        if not status.authenticated:
            raise RuntimeError(status.message or "ChatGPT/Codex 尚未登录。")
        return probe_codex_connection(
            model=selected["codex_model"],
            reasoning_effort=selected["codex_reasoning"],
            speed=selected["codex_speed"],
        )

    from report_interpretation import probe_model_connection

    if provider == PROVIDER_OLLAMA:
        base_key, model_key = "ollama_base_url", "ollama_model"
    else:
        base_key, model_key = cloud_preference_keys(provider)
    return probe_model_connection(
        provider=provider,
        base_url=selected[base_key],
        model=selected[model_key],
        api_key=api_key,
    )


def start_model_connection_test(
    preferences: Mapping[str, object] | None = None,
    *,
    api_key: str = "",
    force: bool = False,
    background: bool = True,
) -> dict[str, str] | None:
    """Test the selected saved route once per app process and configuration."""
    selected = normalize_interpretation_preferences(preferences or load_interpretation_preferences())
    if selected["mode"] != MODE_LLM:
        return None

    fingerprint = preference_connection_fingerprint(selected, api_key=api_key)
    provider = selected["provider"]
    if provider in CLOUD_API_PROVIDERS and not api_key.strip():
        item = _result(
            fingerprint,
            "needs_api_key",
            "已恢复云端 API 选择。为安全起见 API Key 不保存，请重新输入后验证连接。",
        )
        with _connection_lock:
            _connection_results[fingerprint] = item
        return item.public_dict()

    with _connection_lock:
        existing = _connection_results.get(fingerprint)
        if existing is not None and not force:
            return existing.public_dict()
        testing = _result(fingerprint, "testing", "软件启动后正在自动测试所选模型连接…")
        _connection_results[fingerprint] = testing

    def run_probe() -> None:
        try:
            detail = _probe_connection(selected, api_key)
            final = _result(fingerprint, "ok", detail)
        except Exception as exc:
            final = _result(fingerprint, "error", str(exc))
        with _connection_lock:
            _connection_results[fingerprint] = final

    if background:
        threading.Thread(
            target=run_probe,
            name="MyBioToolsModelConnectionTest",
            daemon=True,
        ).start()
        return testing.public_dict()

    run_probe()
    return get_model_connection_test_result(fingerprint)


def start_saved_model_connection_test() -> dict[str, str] | None:
    """Desktop-startup entry point; safe to call on every Streamlit rerun."""
    return start_model_connection_test(load_interpretation_preferences(), background=True)


def _reset_model_connection_tests_for_testing() -> None:
    with _connection_lock:
        _connection_results.clear()


__all__ = [
    "CONFIG_DIR_ENV",
    "DEFAULT_INTERPRETATION_PREFERENCES",
    "MODE_LLM",
    "MODE_RULES",
    "ModelConnectionTestResult",
    "PROVIDER_CODEX_CHATGPT",
    "PROVIDER_CHATANYWHERE",
    "PROVIDER_DEEPSEEK",
    "PROVIDER_DOUBAO",
    "PROVIDER_OLLAMA",
    "PROVIDER_OPENAI_COMPATIBLE",
    "PROVIDER_QWEN",
    "PROVIDER_ZHIPU",
    "get_model_connection_test_result",
    "load_interpretation_preferences",
    "model_connection_fingerprint",
    "normalize_interpretation_preferences",
    "preference_connection_fingerprint",
    "preferences_path",
    "record_model_connection_test_result",
    "save_interpretation_preferences",
    "start_model_connection_test",
    "start_saved_model_connection_test",
]
