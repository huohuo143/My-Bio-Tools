"""Persistent, non-sensitive appearance preferences for My Bio Tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

from model_preferences import preferences_path


APPEARANCE_SYSTEM = "system"
APPEARANCE_LIGHT = "light"
APPEARANCE_DARK = "dark"
DEFAULT_APPEARANCE = APPEARANCE_SYSTEM
APPEARANCE_LABELS = {
    APPEARANCE_SYSTEM: "跟随系统",
    APPEARANCE_LIGHT: "日间",
    APPEARANCE_DARK: "夜间",
}


def normalize_appearance_mode(value: object) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in APPEARANCE_LABELS else DEFAULT_APPEARANCE


def appearance_preferences_path(
    *,
    environ: Mapping[str, str] | None = None,
    system_name: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return a separate file so model and appearance writes cannot collide."""
    return preferences_path(
        environ=environ,
        system_name=system_name,
        home=home,
    ).with_name("appearance.json")


def load_appearance_mode(*, path: Path | None = None) -> str:
    target = path or appearance_preferences_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return DEFAULT_APPEARANCE
    if not isinstance(payload, dict):
        return DEFAULT_APPEARANCE
    return normalize_appearance_mode(payload.get("mode"))


def save_appearance_mode(mode: object, *, path: Path | None = None) -> str:
    """Atomically persist only the normalized appearance mode."""
    selected = normalize_appearance_mode(mode)
    target = path or appearance_preferences_path()
    target.parent.mkdir(parents=True, exist_ok=True)
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
            json.dump({"mode": selected}, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass
    return selected
