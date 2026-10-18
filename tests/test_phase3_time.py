"""Phase 3: time-interval parsing and UTC window resolution."""
from datetime import datetime, timedelta, timezone

import pytest

from timeutil import (
    Window,
    get_tz,
    parse_duration,
    parse_local_datetime,
    resolve_window,
)

IST = get_tz("Asia/Kolkata")
UTC = timezone.utc


# --------------------------------------------------------------------------
# Duration parsing
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [
        ("6h", timedelta(hours=6)),
        ("24h", timedelta(hours=24)),
        ("3d", timedelta(days=3)),
        ("1w", timedelta(days=7)),
        ("12H", timedelta(hours=12)),
        ("3D", timedelta(days=3)),
        (" 8h ", timedelta(hours=8)),
    ],
)
def test_parse_duration_ok(text, expected):
    assert parse_duration(text) == expected


def test_parse_duration_zero_is_allowed_but_yields_empty_window():
    window = resolve_window(last="0h", tz=IST, now=datetime(2026, 10, 18, tzinfo=UTC))
    assert window.since == window.until  # zero-length window, no crash


@pytest.mark.parametrize("text", ["-3d", "3x", "soon", "3 days", "", "h", "3.5h"])
def test_parse_duration_rejects_bad(text):
    with pytest.raises(ValueError):
        parse_duration(text)


def test_parse_duration_very_large_is_fine():
    assert parse_duration("99999d") == timedelta(days=99999)


# --------------------------------------------------------------------------
# Local datetime parsing / IST offset
# --------------------------------------------------------------------------
@pytest.mark.parametrize("month", [1, 6, 12])
def test_ist_offset_is_plus_5_30_year_round_no_dst(month):
    moment = parse_local_datetime(f"2026-{month:02d}-15 06:00", IST)
    assert moment.utcoffset() == timedelta(hours=5, minutes=30)


@pytest.mark.parametrize("text", ["2026-13-01 00:00", "2026-10-18 25:00", "18/10/2026", "tomorrow"])
def test_parse_local_datetime_rejects_bad(text):
    with pytest.raises(ValueError):
        parse_local_datetime(text, IST)


# --------------------------------------------------------------------------
# Window resolution
# --------------------------------------------------------------------------
def test_today_starts_at_midnight_ist():
    now = datetime(2026, 10, 18, 13, 0, tzinfo=UTC)  # 18:30 IST
    window = resolve_window(today=True, tz=IST, now=now)
    assert window.since == datetime(2026, 10, 17, 18, 30, tzinfo=UTC)  # 00:00 IST
    assert window.until == now


def test_yesterday_is_a_full_ist_day():
    now = datetime(2026, 10, 18, 13, 0, tzinfo=UTC)
    window = resolve_window(yesterday=True, tz=IST, now=now)
    assert window.since == datetime(2026, 10, 16, 18, 30, tzinfo=UTC)
    assert (window.until - window.since) >= timedelta(hours=23, minutes=59)


def test_last_window_crossing_midnight():
    now = datetime(2026, 10, 18, 2, 0, tzinfo=UTC)  # 07:30 IST
    window = resolve_window(last="24h", tz=IST, now=now)
    assert window.since == now - timedelta(hours=24)
    assert window.since.date() != window.until.date()


def test_explicit_range_is_ist_to_utc():
    window = resolve_window(
        since="2026-10-17 06:00",
        until="2026-10-18 18:00",
        tz=IST,
        now=datetime(2026, 10, 20, tzinfo=UTC),
    )
    assert window.since == datetime(2026, 10, 17, 0, 30, tzinfo=UTC)
    assert window.until == datetime(2026, 10, 18, 12, 30, tzinfo=UTC)


def test_since_without_until_uses_now():
    now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    window = resolve_window(since="2026-10-18 00:00", tz=IST, now=now)
    assert window.until == now


def test_since_after_until_errors():
    with pytest.raises(ValueError):
        resolve_window(since="2026-10-18 10:00", until="2026-10-18 09:00", tz=IST)


def test_future_since_alone_errors():
    with pytest.raises(ValueError):
        resolve_window(since="2099-01-01 00:00", tz=IST, now=datetime(2026, 10, 18, tzinfo=UTC))


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"today": True, "yesterday": True},
        {"today": True, "last": "3d"},
        {"last": "3d", "since": "2026-10-18 00:00"},
        {"today": True, "since_last_run": True},
    ],
)
def test_conflicting_or_missing_time_options_error(kwargs):
    with pytest.raises(ValueError):
        resolve_window(tz=IST, now=datetime(2026, 10, 18, tzinfo=UTC), **kwargs)


def test_since_last_run_needs_state():
    with pytest.raises(ValueError):
        resolve_window(since_last_run=True, last_run=None, tz=IST)


def test_since_last_run_ok():
    last = datetime(2026, 10, 17, 0, 0, tzinfo=UTC)
    now = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
    window = resolve_window(since_last_run=True, last_run=last, tz=IST, now=now)
    assert window.since == last and window.until == now


# --------------------------------------------------------------------------
# Awareness
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "window",
    [
        resolve_window(today=True, tz=IST, now=datetime(2026, 10, 18, tzinfo=UTC)),
        resolve_window(last="6h", tz=IST, now=datetime(2026, 10, 18, tzinfo=UTC)),
        resolve_window(since="2026-10-18 00:00", until="2026-10-18 06:00", tz=IST),
    ],
)
def test_window_datetimes_are_timezone_aware_utc(window):
    assert window.since.tzinfo is not None and window.until.tzinfo is not None
    assert window.since.utcoffset() == timedelta(0)
    assert window.until.utcoffset() == timedelta(0)


def test_naive_last_run_is_treated_as_utc():
    last = datetime(2026, 10, 17, 0, 0)  # naive
    now = datetime(2026, 10, 18, 0, 0, tzinfo=UTC)
    window = resolve_window(since_last_run=True, last_run=last, tz=IST, now=now)
    assert window.since.tzinfo is not None


# BUG-11 (fixed): the future/corrupt-timestamp fallback to --last 24h is a
# main-level concern; see test_phase4_integration.py::test_since_last_run_*.


# --------------------------------------------------------------------------
# BUG-10: boundary semantics — Window.contains is dead code and inclusive on
# both ends, while the scraper treats the end as exclusive (offset_date).
# --------------------------------------------------------------------------
def test_window_contains_is_half_open():
    window = Window(datetime(2026, 10, 18, 0, 0, tzinfo=UTC), datetime(2026, 10, 18, 6, 0, tzinfo=UTC))
    assert window.contains(window.since) is True   # start inclusive
    assert window.contains(window.until) is False  # end exclusive


def test_window_contains_is_actually_used_by_scraper():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert ".contains(" in (root / "scraper.py").read_text(encoding="utf-8")
