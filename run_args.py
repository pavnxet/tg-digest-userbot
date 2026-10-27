"""Parse /run, /preview and /schedule arguments into a main.py argv list.

Only validated values ever reach argv, and argv is always a list -- never a
shell string. Unknown tokens are rejected, so ``--auto-join`` can never appear.
"""
from __future__ import annotations

import re
from typing import Callable, Optional

from timeutil import get_tz, parse_local_datetime

WINDOW_TOKENS = ("today", "yesterday", "6h", "24h", "3d", "since-last", "all")
DURATIONS = ("6h", "24h", "3d")          # the examples we show
MAX_DURATION_DAYS = 365
_DURATION_RE = re.compile(r"^(\d{1,4})([hdw])$")


def _duration(token: str) -> Optional[list[str]]:
    """`5d` -> ["--last", "5d"].

    Any <N>h|d|w up to a year, not just the three examples -- `main.py` has
    always accepted these via `parse_duration`; only the bot's allowlist was
    narrower.
    """
    match = _DURATION_RE.match((token or "").strip().lower())
    if match is None:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    hours = amount * {"h": 1, "d": 24, "w": 168}[unit]
    if amount <= 0 or hours > MAX_DURATION_DAYS * 24:
        return None
    return ["--last", f"{amount}{unit}"]


def is_valid_window(token: str) -> bool:
    """True for a keyword window or any <N>h|d|w duration."""
    low = (token or "").strip().lower()
    return low in WINDOW_TOKENS or _duration(low) is not None
_WINDOW_FLAGS = {
    "today": ["--today"],
    "yesterday": ["--yesterday"],
    "since-last": ["--since-last-run"],
    "all": ["--all-history"],
}
_DT_ANY = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2})")
WINDOW_HELP = (
    "window: today · yesterday · since-last · all\n"
    "या कोई अवधि — 6h, 24h, 5d, 1w (h = घंटे, d = दिन, w = हफ़्ते)\n"
    "या दो तारीख़ें: 2026-10-17 06:00 2026-10-18 18:00"
)


class ArgError(Exception):
    """A rejected /run argument; the message is safe to show the owner."""


def parse_window(text: str, tz=None) -> tuple[list[str], str]:
    """Return (argv fragment for the window, remaining text).

    The window may appear **anywhere**, so `/run 3d channel:5` and
    `/run channel:5 3d` both work -- people type it both ways, and rejecting the
    second form was confusing.
    """
    text = text.strip()
    if not text:
        raise ArgError("window नहीं दिया। उदाहरण: /run today")

    # An explicit IST range is two datetimes; accept them wherever they are.
    match = _DT_ANY.search(text)
    if match:
        start, end = match.group(1), match.group(2)
        tz = tz or get_tz()
        try:
            parse_local_datetime(start, tz)   # validate
            parse_local_datetime(end, tz)
        except ValueError as exc:
            raise ArgError(f"तारीख़ गलत: {exc}") from exc
        rest = (text[: match.start()] + " " + text[match.end():]).strip()
        return ["--since", start, "--until", end], rest

    words = text.split()
    for index, word in enumerate(words):
        low = word.lower()
        fragment = _WINDOW_FLAGS.get(low) or _duration(low)
        if fragment is not None:
            rest = " ".join(words[:index] + words[index + 1:]).strip()
            return list(fragment), rest

    # A bare number is almost always a duration missing its unit.
    if any(word.isdigit() for word in words):
        raise ArgError(
            "window में unit डालें — घंटे के लिए h, दिन के लिए d (जैसे 5h या 5d)।\n" + WINDOW_HELP
        )
    raise ArgError("window नहीं मिला।\n" + WINDOW_HELP + "\nउदाहरण: /run 3d channel:5")


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
        if token == "--max-per-channel" and i + 1 < len(argv):  # nosec B105 - an argv flag, not a secret
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
