"""Safe, non-interactive ChatGPT-account access through the local Codex CLI."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import tempfile
import time
from typing import Callable, Mapping, Sequence


PROVIDER_CODEX_CHATGPT = "codex_chatgpt"
CODEX_ACCOUNT_MODEL = "account_default"
CODEX_DEFAULT_REASONING = "model_default"
CODEX_DEFAULT_SPEED = "standard"
CODEX_FAST_SPEED = "fast"
CODEX_TIMEOUT_SECONDS = 240

CODEX_MODEL_OPTIONS: tuple[tuple[str, str], ...] = (
    (CODEX_ACCOUNT_MODEL, "自动选择（跟随当前账号）"),
    ("gpt-5.6-sol", "GPT-5.6 Sol（复杂任务）"),
    ("gpt-5.6-terra", "GPT-5.6 Terra（日常均衡）"),
    ("gpt-5.6-luna", "GPT-5.6 Luna（快速明确任务）"),
    ("gpt-5.5", "GPT-5.5（兼容）"),
    ("gpt-5.2", "GPT-5.2（兼容）"),
)
CODEX_MODEL_LABELS = dict(CODEX_MODEL_OPTIONS)
CODEX_REASONING_LABELS = {
    CODEX_DEFAULT_REASONING: "模型默认（推荐）",
    "low": "低（更快）",
    "medium": "中（均衡）",
    "high": "高（复杂任务）",
    "xhigh": "超高",
    "max": "最大",
}
CODEX_SPEED_LABELS = {
    CODEX_DEFAULT_SPEED: "标准",
    CODEX_FAST_SPEED: "快速（约 1.5×）",
}
_BASE_REASONING_OPTIONS = (CODEX_DEFAULT_REASONING, "low", "medium", "high", "xhigh")
_MAX_REASONING_MODELS = {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
_FAST_MODELS = {
    CODEX_ACCOUNT_MODEL,
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
}

_DISABLED_FEATURES = (
    "plugins",
    "skill_search",
    "memories",
    "apps",
    "shell_tool",
    "unified_exec",
    "computer_use",
    "browser_use",
    "in_app_browser",
    "image_generation",
    "goals",
    "multi_agent",
    "workspace_dependencies",
)
_REQUIRED_EXEC_FLAGS = (
    "--ephemeral",
    "--ignore-user-config",
    "--ignore-rules",
    "--skip-git-repo-check",
    "--output-schema",
    "--json",
)
_REQUIRED_GLOBAL_FLAGS = ("--ask-for-approval", "--disable", "--model", "--config")

CODEX_RESPONSE_SCHEMA: dict[str, object] = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "executive_summary",
        "multiomics_interpretation",
        "haplotype_interpretation",
        "integrated_hypotheses",
    ],
    "properties": {
        "executive_summary": {"type": "string"},
        "multiomics_interpretation": {"type": "string"},
        "haplotype_interpretation": {"type": "string"},
        "integrated_hypotheses": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["hypothesis", "support", "limitations", "experiment"],
                "properties": {
                    "hypothesis": {"type": "string"},
                    "support": {"type": "string"},
                    "limitations": {"type": "string"},
                    "experiment": {"type": "string"},
                },
            },
        },
    },
}


@dataclass(frozen=True)
class CodexClientStatus:
    available: bool
    authenticated: bool
    platform: str
    version: str = ""
    executable: str = ""
    error_code: str = ""
    message: str = ""

    def public_dict(self) -> dict[str, object]:
        """Return UI-safe status without exposing a local executable path."""
        return {
            "available": self.available,
            "authenticated": self.authenticated,
            "platform": self.platform,
            "version": self.version,
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class CodexRunResult:
    payload: dict[str, object]
    client_version: str


class CodexClientError(RuntimeError):
    def __init__(self, code: str, message: str, client_version: str = ""):
        super().__init__(message)
        self.code = code
        self.user_message = message
        self.client_version = client_version


class CodexInvocationCancelled(CodexClientError):
    def __init__(self) -> None:
        super().__init__("cancelled", "ChatGPT/Codex 解读已取消。")


def probe_codex_connection(
    *,
    model: str = CODEX_ACCOUNT_MODEL,
    reasoning_effort: str = CODEX_DEFAULT_REASONING,
    speed: str = CODEX_DEFAULT_SPEED,
    timeout: int = 90,
) -> str:
    """Run a minimal real model turn so the selected Codex route is genuinely verified."""
    result = run_codex_interpretation(
        (
            "这是 My Bio Tools 的连接性测试，不包含科研数据。请仅返回符合输出结构的最小 JSON："
            "executive_summary 写 ok，multiomics_interpretation 与 haplotype_interpretation 留空字符串，"
            "integrated_hypotheses 返回空数组。"
        ),
        model=model,
        reasoning_effort=reasoning_effort,
        speed=speed,
        timeout=timeout,
    )
    if str(result.payload.get("executive_summary") or "").strip().casefold() != "ok":
        raise CodexClientError("invalid_output", "Codex 模型已响应，但连接测试返回格式不正确。")
    return f"{codex_model_label(model)} · {result.client_version}"


def codex_model_label(model: str) -> str:
    return CODEX_MODEL_LABELS.get(model, model)


def codex_reasoning_label(reasoning_effort: str) -> str:
    return CODEX_REASONING_LABELS.get(reasoning_effort, reasoning_effort)


def codex_speed_label(speed: str) -> str:
    return CODEX_SPEED_LABELS.get(speed, speed)


def codex_reasoning_options(model: str) -> tuple[str, ...]:
    if model in _MAX_REASONING_MODELS:
        return (*_BASE_REASONING_OPTIONS, "max")
    return _BASE_REASONING_OPTIONS


def codex_speed_options(model: str) -> tuple[str, ...]:
    if model in _FAST_MODELS:
        return (CODEX_DEFAULT_SPEED, CODEX_FAST_SPEED)
    return (CODEX_DEFAULT_SPEED,)


def _validate_codex_selection(model: str, reasoning_effort: str, speed: str) -> None:
    if model not in CODEX_MODEL_LABELS:
        raise CodexClientError("invalid_configuration", "所选 Codex 模型不受当前版本支持，请重新选择。")
    if reasoning_effort not in codex_reasoning_options(model):
        raise CodexClientError("invalid_configuration", "所选模型不支持该推理档位，请重新选择。")
    if speed not in codex_speed_options(model):
        raise CodexClientError("invalid_configuration", "所选模型不支持快速模式，请改用标准速度。")


def codex_install_guidance(system_name: str | None = None) -> str:
    current = system_name or platform.system()
    if current == "Windows":
        return (
            "请先按 OpenAI 官方方式安装 Codex CLI，在 PowerShell 执行："
            "powershell -ExecutionPolicy ByPass -c \"irm https://chatgpt.com/codex/install.ps1 | iex\"；"
            "随后运行 codex 并选择 Sign in with ChatGPT，最后重启 My Bio Tools。"
        )
    return "请安装或更新官方 ChatGPT 桌面 App，并在 Codex 模式中登录 ChatGPT。"


def _candidate_paths(
    *,
    system_name: str,
    environ: Mapping[str, str],
    which: Callable[[str], str | None],
) -> list[Path]:
    candidates: list[Path] = []
    home = Path(environ.get("HOME") or environ.get("USERPROFILE") or str(Path.home()))
    if system_name == "Darwin":
        candidates.extend(
            [
                Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
                home / "Applications/ChatGPT.app/Contents/Resources/codex",
            ]
        )
    elif system_name == "Windows":
        local_app_data = environ.get("LOCALAPPDATA", "")
        if local_app_data:
            candidates.append(Path(local_app_data) / "Programs/OpenAI/Codex/bin/codex.exe")

    for name in (("codex.exe", "codex") if system_name == "Windows" else ("codex",)):
        resolved = which(name)
        if resolved:
            candidates.append(Path(resolved))

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).casefold() if system_name == "Windows" else str(path)
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _minimal_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Keep authentication/network essentials while excluding API keys and project secrets."""
    original = source if source is not None else os.environ
    allowed = {
        "ALL_PROXY",
        "APPDATA",
        "CODEX_HOME",
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "LOGNAME",
        "NO_PROXY",
        "PATH",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "USERPROFILE",
    }
    return {key: value for key, value in original.items() if key.upper() in allowed}


def _probe(
    command: Sequence[str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
    environ: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return runner(
        list(command),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=8,
        env=_minimal_environment(environ),
        check=False,
    )


def detect_codex_client(
    executable: str | Path | None = None,
    *,
    system_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> CodexClientStatus:
    """Locate a capable Codex CLI and verify ChatGPT subscription authentication."""
    current_system = system_name or platform.system()
    current_env = environ if environ is not None else os.environ
    candidates = [Path(executable)] if executable else _candidate_paths(
        system_name=current_system,
        environ=current_env,
        which=which,
    )
    installed = [path for path in candidates if path.is_file()]
    if not installed:
        return CodexClientStatus(
            False,
            False,
            current_system,
            error_code="not_installed",
            message=codex_install_guidance(current_system),
        )

    failures: list[CodexClientStatus] = []
    for selected in installed:
        try:
            version_result = _probe([str(selected), "--version"], runner=runner, environ=current_env)
            version_text = (version_result.stdout or version_result.stderr or "").strip().splitlines()
            version = version_text[0] if version_text else ""
            if version_result.returncode != 0 or not version:
                raise OSError("version probe failed")

            global_help = _probe([str(selected), "--help"], runner=runner, environ=current_env)
            exec_help = _probe([str(selected), "exec", "--help"], runner=runner, environ=current_env)
            help_text = f"{exec_help.stdout}\n{exec_help.stderr}"
            global_help_text = f"{global_help.stdout}\n{global_help.stderr}"
            missing = [flag for flag in _REQUIRED_EXEC_FLAGS if flag not in help_text]
            missing.extend(flag for flag in _REQUIRED_GLOBAL_FLAGS if flag not in global_help_text)
            if global_help.returncode != 0 or exec_help.returncode != 0 or missing:
                failures.append(
                    CodexClientStatus(
                        True,
                        False,
                        current_system,
                        version=version,
                        executable=str(selected),
                        error_code="unsupported_client",
                        message="Codex CLI 版本过旧或缺少安全调用参数，请先更新后再刷新检测。",
                    )
                )
                continue

            login_result = _probe([str(selected), "login", "status"], runner=runner, environ=current_env)
            login_text = f"{login_result.stdout}\n{login_result.stderr}"
            authenticated = login_result.returncode == 0 and "logged in using chatgpt" in login_text.casefold()
            if not authenticated:
                failures.append(
                    CodexClientStatus(
                        True,
                        False,
                        current_system,
                        version=version,
                        executable=str(selected),
                        error_code="not_authenticated",
                        message="已检测到 Codex CLI，但尚未使用 ChatGPT 登录；请运行 codex 并选择 Sign in with ChatGPT。",
                    )
                )
                continue
            return CodexClientStatus(
                True,
                True,
                current_system,
                version=version,
                executable=str(selected),
                message=f"已使用 ChatGPT 登录（{version}）。",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            failures.append(
                CodexClientStatus(
                    True,
                    False,
                    current_system,
                    executable=str(selected),
                    error_code="client_unavailable",
                    message=f"Codex CLI 检测失败：{type(exc).__name__}。",
                )
            )

    priority = {"not_authenticated": 0, "unsupported_client": 1, "client_unavailable": 2}
    return min(failures, key=lambda item: priority.get(item.error_code, 99))


def _classify_cli_error(text: str) -> tuple[str, str]:
    lowered = text.casefold()
    if any(token in lowered for token in ("usage limit", "rate limit", "quota", "credit", "exhausted")):
        return "usage_limit", "ChatGPT/Codex 使用额度当前不可用，请稍后重试或改用离线规则。"
    if any(token in lowered for token in ("not logged", "sign in", "unauthorized", "authentication", "workspace_disabled")):
        return "not_authenticated", "Codex CLI 的 ChatGPT 登录不可用，请重新登录后刷新检测。"
    return "process_error", "ChatGPT/Codex 解读进程未成功完成，已保留离线规则解读。"


def parse_codex_jsonl(text: str) -> str:
    """Extract the final agent JSON text from `codex exec --json` output."""
    messages: list[str] = []
    errors: list[str] = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") == "agent_message" and str(item.get("text") or "").strip():
            messages.append(str(item["text"]).strip())
        elif item.get("type") == "error":
            errors.append(str(item.get("message") or ""))
    if messages:
        return messages[-1]
    code, message = _classify_cli_error("\n".join(errors))
    raise CodexClientError(code, message)


def _validated_payload(text: str) -> dict[str, object]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CodexClientError("invalid_output", "ChatGPT/Codex 未返回可解析的结构化结果。") from exc
    if not isinstance(payload, dict):
        raise CodexClientError("invalid_output", "ChatGPT/Codex 返回结果不是 JSON 对象。")
    for key in ("executive_summary", "multiomics_interpretation", "haplotype_interpretation"):
        if not isinstance(payload.get(key), str):
            raise CodexClientError("invalid_output", f"ChatGPT/Codex 返回结果缺少字段：{key}。")
    hypotheses = payload.get("integrated_hypotheses")
    if not isinstance(hypotheses, list) or any(not isinstance(item, dict) for item in hypotheses):
        raise CodexClientError("invalid_output", "ChatGPT/Codex 候选假设格式不正确。")
    return payload


def _terminate_process(process: subprocess.Popen[str], system_name: str) -> None:
    if process.poll() is not None:
        return
    try:
        if system_name != "Windows":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=2)
    except (OSError, subprocess.SubprocessError):
        try:
            if system_name != "Windows":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except OSError:
            pass


def _communicate_with_cancel(
    process: subprocess.Popen[str],
    prompt: str,
    *,
    timeout: int,
    is_cancelled: Callable[[], bool] | None,
    system_name: str,
) -> tuple[str, str]:
    deadline = time.monotonic() + max(1, timeout)
    pending_input: str | None = prompt
    while True:
        if is_cancelled and is_cancelled():
            _terminate_process(process, system_name)
            raise CodexInvocationCancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process(process, system_name)
            raise CodexClientError("timeout", "ChatGPT/Codex 解读超时，已保留离线规则解读。")
        try:
            return process.communicate(input=pending_input, timeout=min(0.25, remaining))
        except subprocess.TimeoutExpired:
            pending_input = None


def run_codex_interpretation(
    prompt: str,
    *,
    model: str = CODEX_ACCOUNT_MODEL,
    reasoning_effort: str = CODEX_DEFAULT_REASONING,
    speed: str = CODEX_DEFAULT_SPEED,
    status: CodexClientStatus | None = None,
    timeout: int = CODEX_TIMEOUT_SECONDS,
    is_cancelled: Callable[[], bool] | None = None,
    system_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    popen_factory: Callable[..., subprocess.Popen[str]] = subprocess.Popen,
) -> CodexRunResult:
    """Run one ephemeral, tool-disabled Codex turn and return strict JSON."""
    _validate_codex_selection(model, reasoning_effort, speed)
    current_system = system_name or platform.system()
    client = status or detect_codex_client(system_name=current_system, environ=environ)
    if not client.available:
        raise CodexClientError(
            client.error_code or "not_installed",
            client.message or codex_install_guidance(current_system),
            client.version,
        )
    if not client.authenticated or not client.executable:
        raise CodexClientError(
            client.error_code or "not_authenticated",
            client.message or "Codex CLI 尚未使用 ChatGPT 登录。",
            client.version,
        )

    with tempfile.TemporaryDirectory(prefix="my-bio-tools-codex-") as temporary:
        workdir = Path(temporary)
        schema_path = workdir / "response.schema.json"
        schema_path.write_text(json.dumps(CODEX_RESPONSE_SCHEMA, ensure_ascii=False), encoding="utf-8")
        try:
            schema_path.chmod(0o600)
        except OSError:
            pass

        command = [str(client.executable), "--ask-for-approval", "never"]
        for feature in _DISABLED_FEATURES:
            command.extend(["--disable", feature])
        if model != CODEX_ACCOUNT_MODEL:
            command.extend(["--model", model])
        if reasoning_effort != CODEX_DEFAULT_REASONING:
            command.extend(["--config", f'model_reasoning_effort="{reasoning_effort}"'])
        if speed == CODEX_FAST_SPEED:
            command.extend(["--config", "features.fast_mode=true"])
            command.extend(["--config", 'service_tier="fast"'])
        command.extend(
            [
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "-C",
                str(workdir),
                "--sandbox",
                "read-only",
                "--output-schema",
                str(schema_path),
                "--json",
                "-",
            ]
        )
        popen_kwargs: dict[str, object] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "env": _minimal_environment(environ),
        }
        if current_system == "Windows":
            popen_kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        else:
            popen_kwargs["start_new_session"] = True

        try:
            process = popen_factory(command, **popen_kwargs)
        except OSError as exc:
            raise CodexClientError("client_unavailable", "无法启动 ChatGPT/Codex 解读进程。") from exc
        try:
            stdout, stderr = _communicate_with_cancel(
                process,
                prompt,
                timeout=timeout,
                is_cancelled=is_cancelled,
                system_name=current_system,
            )
        except CodexClientError as exc:
            exc.client_version = client.version
            raise
        if process.returncode != 0:
            code, message = _classify_cli_error(stderr or stdout)
            raise CodexClientError(code, message, client.version)
        try:
            payload = _validated_payload(parse_codex_jsonl(stdout))
        except CodexClientError as exc:
            exc.client_version = client.version
            raise
        return CodexRunResult(payload=payload, client_version=client.version)


__all__ = [
    "CODEX_ACCOUNT_MODEL",
    "CODEX_DEFAULT_REASONING",
    "CODEX_DEFAULT_SPEED",
    "CODEX_FAST_SPEED",
    "CODEX_MODEL_OPTIONS",
    "CODEX_REASONING_LABELS",
    "CODEX_RESPONSE_SCHEMA",
    "CODEX_SPEED_LABELS",
    "CodexClientError",
    "CodexClientStatus",
    "CodexInvocationCancelled",
    "CodexRunResult",
    "PROVIDER_CODEX_CHATGPT",
    "codex_install_guidance",
    "codex_model_label",
    "codex_reasoning_label",
    "codex_reasoning_options",
    "codex_speed_label",
    "codex_speed_options",
    "detect_codex_client",
    "parse_codex_jsonl",
    "run_codex_interpretation",
]
