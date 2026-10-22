"""Parse /run, /preview and /schedule arguments into a main.py argv list.

Only validated values ever reach argv, and argv is always a list -- never a
shell string. Unknown tokens are rejected, so ``--auto-join`` can never appear.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from timeutil import get_tz, parse_local_datetime

WINDOW_TOKENS = ("today", "yesterday", "6h", "24h", "3d", "since-last", "all")
DURATIONS = ("6h", "24h", "3d")
_DT = re.compile(r"^\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*(.*)$")


class ArgError(Exception):
    """A rejected /run argument; the message is safe to show the owner."""


def parse_window(text: str, tz=None) -> tuple[list[str], str]:
    """Return (argv fragment for the window, remaining text)."""
    text = text.strip()
    match = _DT.match(text)
    if match:
        start, end, rest = match.group(1), match.group(2), match.group(3)
        tz = tz or get_tz()
        try:
            parse_local_datetime(start, tz)  # validate
            parse_local_datetime(end, tz)
        except ValueError as exc:
            raise ArgError(f"तारीख़ गलत: {exc}") from exc
        return ["--since", start, "--until", end], rest
    parts = text.split(None, 1)
    if not parts:
        raise ArgError("window नहीं दिया। उदाहरण: /run today")
    token = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""
    if token == "today":  # nosec B105 - a window keyword, not a password
        return ["--today"], rest
    if token == "yesterday":  # nosec B105 - window keyword
        return ["--yesterday"], rest
    if token in DURATIONS:
        return ["--last", token], rest
    if token == "since-last":  # nosec B105 - window keyword
        return ["--since-last-run"], rest
    if token == "all":  # nosec B105 - window keyword (the channel's full history)
        return ["--all-history"], rest
    raise ArgError("window: today | yesterday | 6h | 24h | 3d | since-last | all | <YYYY-MM-DD HH:MM> x2")


def _int(value: str, lo: int, hi: int, name: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise ArgError(f"{name}: संख्या चाहिए।") from exc
    if not (lo <= number <= hi):
        raise ArgError(f"{name}: {lo}-{hi} के बीच होना चाहिए।")
    return number


def _force_no_message_cap(argv: list[str]) -> None:
    """Replace the settings' --max-per-channel value with 0 (no cap).

    A full-history run must not stop at the default 5000-message cap, and the
    settings-derived flags would otherwise re-impose it.
    """
    for i, token in enumerate(argv):
        if token == "--max-per-channel" and i + 1 < len(argv):
            argv[i + 1] = "0"


def build_argv(
    text: str,
    settings,
    *,
    subset_writer: Optional[Callable[[str], tuple[Optional[str], Optional[str]]]] = None,
) -> tuple[list[str], bool]:
    """Return (argv, send_files). Raises ArgError on anything invalid."""
    try:
        window, rest = parse_window(text)
    except ValueError as exc:  # defensive: parse_window raises ArgError itself
        raise ArgError(f"window तारीख़ गलत: {exc}") from exc

    argv: list[str] = list(window)
    argv += settings.as_run_flags()          # defaults first, so explicit options win
    all_history = "--all-history" in argv
    if all_history:
        _force_no_message_cap(argv)
    # A full history can be enormous, so it is not sent to the chat unless the
    # owner explicitly asks for it with `send:yes`.
    send_files = not all_history

    for token in rest.split():
        low = token.lower()
        if low == "dedupe":
            argv.append("--dedupe")
        elif low == "resume":
            argv.append("--resume")          # continue a run that was stopped midway
        elif low == "views":
            argv.append("--with-views")
        elif low.startswith("min:"):
            argv += ["--min-chars", str(_int(token[4:], 0, 500, "min"))]
        elif low.startswith("send:"):
            value = token[5:].lower()
            if value not in ("no", "yes"):
                raise ArgError("send:no या send:yes")
            send_files = value == "yes"
        elif low.startswith("group:") or low.startswith("channel:"):
            if subset_writer is None:
                raise ArgError("group/channel filter यहाँ उपलब्ध नहीं।")
            path, error = subset_writer(low)
            if error:
                raise ArgError(error)
            argv += ["--channels-file", str(path)]
        else:
            raise ArgError(f"अज्ञात option: {token}")

    return argv, send_files
