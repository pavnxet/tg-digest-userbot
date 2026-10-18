"""Validated settings for the control panel (section E).

Every key has a default, a validator and an error message. Unknown keys and
invalid values are rejected without changing anything. Stored in settings.json
(git-ignored).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

_log = logging.getLogger("tgdigest.bot.settings")

DEFAULTS: dict[str, Any] = {
    "format": "json",
    "dedupe": "off",
    "min_chars": 0,
    "with_views": "off",
    "max_chars": 300_000,
    "autodelete_minutes": 0,
    "retention_days": 7,
    "max_per_channel": 5000,
}


def _choice(options: tuple[str, ...]) -> Callable[[str], Any]:
    def check(value: str) -> str:
        low = value.strip().lower()
        if low not in options:
            raise ValueError("मान इनमें से होना चाहिए: " + ", ".join(options))
        return low

    return check


def _int_range(lo: int, hi: int) -> Callable[[str], int]:
    def check(value: str) -> int:
        try:
            number = int(str(value).strip())
        except ValueError as exc:
            raise ValueError("संख्या चाहिए।") from exc
        if not (lo <= number <= hi):
            raise ValueError(f"{lo}-{hi} के बीच होना चाहिए।")
        return number

    return check


VALIDATORS: dict[str, Callable[[str], Any]] = {
    "format": _choice(("json", "jsonl", "txt")),
    "dedupe": _choice(("on", "off")),
    "min_chars": _int_range(0, 500),
    "with_views": _choice(("on", "off")),
    "max_chars": _int_range(50_000, 1_000_000),
    "autodelete_minutes": _int_range(0, 1440),
    "retention_days": _int_range(1, 60),
    "max_per_channel": _int_range(100, 20_000),
}


class SettingsStore:
    """Loads, validates and saves the settings."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, Any] = dict(DEFAULTS)
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for key, value in raw.items():
            if key in VALIDATORS:
                try:
                    self._data[key] = VALIDATORS[key](value)
                except ValueError:
                    _log.warning("ignoring invalid stored setting %r", key)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data), encoding="utf-8")

    # -- api ---------------------------------------------------------------
    def get(self, key: str) -> Any:
        return self._data.get(key, DEFAULTS.get(key))

    def all(self) -> dict[str, Any]:
        return dict(self._data)

    def set(self, key: str, value: str) -> tuple[bool, str]:
        if key not in VALIDATORS:
            return False, f"अज्ञात key: {key}"
        try:
            parsed = VALIDATORS[key](value)
        except ValueError as exc:
            return False, f"{key}: {exc}"
        self._data[key] = parsed
        self._save()
        return True, f"{key} = {parsed}"

    def as_run_flags(self) -> list[str]:
        """main.py argv defaults derived from the settings."""
        flags: list[str] = [
            "--format", str(self.get("format")),
            "--max-chars", str(self.get("max_chars")),
            "--max-per-channel", str(self.get("max_per_channel")),
        ]
        if self.get("dedupe") == "on":
            flags.append("--dedupe")
        if int(self.get("min_chars")) > 0:
            flags += ["--min-chars", str(self.get("min_chars"))]
        if self.get("with_views") == "on":
            flags.append("--with-views")
        return flags
