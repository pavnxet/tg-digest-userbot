"""Time-interval parsing and UTC conversion."""
from datetime import datetime, timedelta, timezone

import pytest

from timeutil import (
    get_tz,
    parse_duration,
    parse_local_datetime,
    resolve_window,
)

IST = get_tz("Asia/Kolkata")


def test_parse_duration_units():
    assert parse_duration("6h") == timedelta(hours=6)
    assert parse_duration("24h") == timedelta(hours=24)
    assert parse_duration("3d") == timedelta(days=3)
    assert parse_duration("1w") == timedelta(days=7)
    assert parse_duration(" 12H ") == timedelta(hours=12)


def test_parse_duration_rejects_garbage():
    with pytest.raises(ValueError):
        parse_duration("3 days")
    with pytest.raises(ValueError):
        parse_duration("soon")


def test_parse_local_datetime_is_ist():
    moment = parse_local_datetime("2026-10-17 06:00", IST)
    assert moment.utcoffset() == timedelta(hours=5, minutes=30)
    assert moment.hour == 6


def test_last_24h_window():
    now = datetime(2026, 10, 18, 13, 0, tzinfo=timezone.utc)
    window = resolve_window(last="24h", tz=IST, now=now)
    assert window.until == now
    assert window.since == now - timedelta(hours=24)


def test_today_starts_at_midnight_ist():
    now = datetime(2026, 10, 18, 13, 0, tzinfo=timezone.utc)  # 18:30 IST
    window = resolve_window(today=True, tz=IST, now=now)
    # 00:00 IST == 18:30 UTC on the previous day.
    assert window.since == datetime(2026, 10, 17, 18, 30, tzinfo=timezone.utc)
    assert window.until == now


def test_yesterday_window_is_a_full_day():
    now = datetime(2026, 10, 18, 13, 0, tzinfo=timezone.utc)
    window = resolve_window(yesterday=True, tz=IST, now=now)
    assert window.since < window.until
    assert (window.until - window.since) >= timedelta(hours=23, minutes=59)


def test_explicit_since_until_is_interpreted_as_ist():
    window = resolve_window(
        since="2026-10-17 06:00", until="2026-10-18 18:00", tz=IST,
        now=datetime(2026, 10, 18, 20, 0, tzinfo=timezone.utc),
    )
    assert window.since == datetime(2026, 10, 17, 0, 30, tzinfo=timezone.utc)
    assert window.until == datetime(2026, 10, 18, 12, 30, tzinfo=timezone.utc)


def test_exactly_one_option_is_required():
    with pytest.raises(ValueError):
        resolve_window(tz=IST)
    with pytest.raises(ValueError):
        resolve_window(today=True, yesterday=True, tz=IST)


def test_until_must_be_after_since():
    with pytest.raises(ValueError):
        resolve_window(since="2026-10-18 10:00", until="2026-10-18 09:00", tz=IST)


def test_since_last_run_uses_last_run():
    last = datetime(2026, 10, 17, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 10, 18, 0, 0, tzinfo=timezone.utc)
    window = resolve_window(since_last_run=True, last_run=last, tz=IST, now=now)
    assert window.since == last
    assert window.until == now


def test_since_last_run_without_state_errors():
    with pytest.raises(ValueError):
        resolve_window(since_last_run=True, last_run=None, tz=IST)
