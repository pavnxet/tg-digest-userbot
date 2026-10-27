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

from prompts import available_names

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
    "summary_prompt": "default",
}


def _prompt_choice(value: str) -> str:
    """A prompt name that must exist in prompts/."""
    name = str(value).strip()
    names = available_names()
    if name not in names:
        raise ValueError("उपलब्ध prompts: " + (", ".join(names) if names else "कोई नहीं"))
    return name


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


# key -> (what it does, allowed values). Shown by /settings so the owner never
# has to guess a range.
SETTING_HELP: dict[str, tuple[str, str]] = {
    "format": ("output file format", "json · jsonl · txt"),
    "dedupe": ("drop near-duplicate messages", "on · off"),
    "min_chars": ("skip messages shorter than this", "0-500"),
    "with_views": ("include view counts", "on · off"),
    "max_chars": ("split into parts above this size", "50000-1000000"),
    "autodelete_minutes": ("delete my sent digests after N minutes (0 = off)", "0-1440"),
    "retention_days": ("delete stored digests older than N days", "1-60"),
    "max_per_channel": ("max messages read per channel", "100-20000"),
    "summary_prompt": ("prompt embedded in new digests", "a name from /prompts"),
}


VALIDATORS: dict[str, Callable[[str], Any]] = {
    "format": _choice(("json", "jsonl", "txt")),
    "dedupe": _choice(("on", "off")),
    "min_chars": _int_range(0, 500),
    "with_views": _choice(("on", "off")),
    "max_chars": _int_range(50_000, 1_000_000),
    "autodelete_minutes": _int_range(0, 1440),
    "retention_days": _int_range(1, 60),
    "max_per_channel": _int_range(100, 20_000),
    "summary_prompt": _prompt_choice,
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
            return False, (
                f"अज्ञात key: {key}\n"
                "मान्य keys: " + ", ".join(sorted(VALIDATORS))
            )
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
        # The prompt name is validated on set/load, so only a real prompts/ name
        # can ever reach argv here (chat text cannot add this flag).
        flags += ["--summary-prompt", str(self.get("summary_prompt"))]
        return flags
