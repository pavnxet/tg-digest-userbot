"""Time-window parsing and conversion.

Rule of the whole project: every timestamp is compared internally as a
timezone-aware UTC datetime, and only rendered in IST for the output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

IST_NAME = "Asia/Kolkata"
UTC = timezone.utc

_DURATION_RE = re.compile(r"^\s*(\d+)\s*([hdw])\s*$", re.IGNORECASE)
_UNIT_HOURS = {"h": 1, "d": 24, "w": 24 * 7}

_LOCAL_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


def get_tz(name: str = IST_NAME):
    """Return a tzinfo for `name`, falling back to a fixed IST offset.

    The fallback matters on minimal systems that ship without the IANA
    tzdata package; IST is always UTC+05:30.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        if name == IST_NAME:
            return timezone(timedelta(hours=5, minutes=30), "IST")
        raise


def parse_duration(text: str) -> timedelta:
    """Parse a duration like '6h', '24h', '3d', '1w' into a timedelta."""
    match = _DURATION_RE.match(text)
    if not match:
        raise ValueError(f"Bad duration {text!r}; use forms like 6h, 24h, 3d or 1w.")
    amount = int(match.group(1))
    unit = match.group(2).lower()
    return timedelta(hours=amount * _UNIT_HOURS[unit])


def parse_local_datetime(text: str, tz) -> datetime:
    """Parse 'YYYY-MM-DD HH:MM' (or date-only) as a wall-clock time in `tz`."""
    text = text.strip()
    for fmt in _LOCAL_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    raise ValueError(
        f"Bad datetime {text!r}; expected 'YYYY-MM-DD HH:MM' (interpreted as {tz})."
    )


@dataclass(frozen=True)
class Window:
    """A half-open time window in UTC: since <= t <= until."""

    since: datetime  # timezone-aware UTC
    until: datetime  # timezone-aware UTC

    def contains(self, moment: datetime) -> bool:
        return self.since <= moment <= self.until


def _as_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def resolve_window(
    *,
    today: bool = False,
    yesterday: bool = False,
    last: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    since_last_run: bool = False,
    last_run: Optional[datetime] = None,
    tz=None,
    now: Optional[datetime] = None,
) -> Window:
    """Turn the CLI time options into a concrete UTC Window.

    Exactly one of the options must be chosen. `now` is injectable so the
    function is deterministic under test.
    """
    tz = tz or get_tz()
    if now is None:
        now = datetime.now(UTC)
    now = _as_utc(now)

    options_chosen = sum(
        bool(x)
        for x in (
            today,
            yesterday,
            bool(last),
            bool(since or until),
            since_last_run,
        )
    )
    if options_chosen != 1:
        raise ValueError(
            "Choose exactly one time option: --today, --yesterday, --last, "
            "--since/--until, or --since-last-run."
        )

    if today:
        start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        return Window(_as_utc(start), now)

    if yesterday:
        local_yesterday = now.astimezone(tz) - timedelta(days=1)
        start = local_yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        end = local_yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
        return Window(_as_utc(start), _as_utc(end))

    if last:
        return Window(now - parse_duration(last), now)

    if since_last_run:
        if last_run is None:
            raise ValueError(
                "--since-last-run needs a previous successful run in state.json; "
                "none was found."
            )
        return Window(_as_utc(last_run), now)

    if not since:
        raise ValueError("--since is required when using an explicit range.")
    since_dt = parse_local_datetime(since, tz)
    until_dt = parse_local_datetime(until, tz) if until else now.astimezone(tz)
    if until_dt <= since_dt:
        raise ValueError("--until must be later than --since.")
    return Window(_as_utc(since_dt), _as_utc(until_dt))
