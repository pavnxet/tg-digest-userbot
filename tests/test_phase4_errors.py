"""Phase 4: per-channel error handling — the run must never crash as a whole."""
from datetime import datetime, timedelta, timezone

import pytest
from telethon.errors import (
    ChannelBannedError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
    InviteHashExpiredError,
    UsernameNotOccupiedError,
)

from conftest import run_main
from fake_client import make_channel, make_message
from timeutil import get_tz

IST = get_tz()
BASE = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def ist(dt):
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M")


WINDOW = ["--since", ist(BASE - timedelta(minutes=10)), "--until", ist(BASE + timedelta(minutes=1))]


def load_digest(project):
    import json

    return json.loads(sorted(project["out"].glob("*.json"))[0].read_text(encoding="utf-8"))


@pytest.fixture
def two_channels(fake_client):
    good = make_channel(111, "Good", username="good")
    bad = make_channel(222, "Bad", username="bad")
    fake_client.add_channel(good, [make_message(1, BASE, text="ok")])
    fake_client.add_channel(bad, [make_message(2, BASE, text="ok")])
    return good, bad


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    # Never actually sleep during error tests.
    async def _no_sleep(seconds):
        return None

    monkeypatch.setattr("scraper.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("main.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("channels.asyncio.sleep", _no_sleep)


@pytest.mark.parametrize(
    "error",
    [
        ChannelPrivateError(request=None),
        ChannelBannedError(request=None),
        ChatAdminRequiredError(request=None),
    ],
)
def test_access_errors_skip_channel(project, fake_client, monkeypatch, two_channels, error):
    good, bad = two_channels
    fake_client.iter_errors[222] = error
    run_main(monkeypatch, project, fake_client, WINDOW, "@good\n@bad\n")
    digest = load_digest(project)
    assert digest["meta"]["channels_ok"] == 1
    assert any(s["reason"] == "no_access" for s in digest["meta"]["skipped"])


def test_short_flood_wait_retries_then_skips(project, fake_client, monkeypatch, two_channels):
    fake_client.iter_errors[222] = FloodWaitError(request=None, capture=30)
    run_main(monkeypatch, project, fake_client, WINDOW, "@good\n@bad\n")
    digest = load_digest(project)
    assert digest["meta"]["channels_ok"] == 1
    assert any(s["reason"] == "flood_wait" for s in digest["meta"]["skipped"])


def test_long_flood_wait_within_limit_skips(project, fake_client, monkeypatch, two_channels):
    fake_client.iter_errors[222] = FloodWaitError(request=None, capture=300)
    run_main(monkeypatch, project, fake_client, WINDOW + ["--max-flood-wait", "900"], "@good\n@bad\n")
    assert any(s["reason"] == "flood_wait" for s in load_digest(project)["meta"]["skipped"])


def test_flood_wait_above_limit_skips_immediately(project, fake_client, monkeypatch, two_channels):
    fake_client.iter_errors[222] = FloodWaitError(request=None, capture=5000)
    run_main(monkeypatch, project, fake_client, WINDOW + ["--max-flood-wait", "900"], "@good\n@bad\n")
    assert any(s["reason"] == "flood_wait" for s in load_digest(project)["meta"]["skipped"])


def test_network_error_gives_up_after_retries(project, fake_client, monkeypatch, two_channels):
    fake_client.iter_errors[222] = ConnectionError("net down")
    run_main(monkeypatch, project, fake_client, WINDOW, "@good\n@bad\n")
    assert any(s["reason"].startswith("network") for s in load_digest(project)["meta"]["skipped"])


def test_username_not_occupied_is_skipped(project, fake_client, monkeypatch):
    good = make_channel(111, "Good", username="good")
    fake_client.add_channel(good, [make_message(1, BASE, text="ok")])
    fake_client.get_entity_errors["missing"] = UsernameNotOccupiedError(request=None)
    run_main(monkeypatch, project, fake_client, WINDOW, "@good\n@missing\n")
    digest = load_digest(project)
    assert digest["meta"]["channels_ok"] == 1
    assert any(s["reason"] == "not_found" for s in digest["meta"]["skipped"])


def test_invite_hash_expired_is_skipped(project, fake_client, monkeypatch):
    good = make_channel(111, "Good", username="good")
    fake_client.add_channel(good, [make_message(1, BASE, text="ok")])
    fake_client.invite_errors["DEAD"] = InviteHashExpiredError(request=None)
    run_main(monkeypatch, project, fake_client, WINDOW, "@good\nhttps://t.me/+DEAD\n")
    assert any(s["reason"] == "invite_expired" for s in load_digest(project)["meta"]["skipped"])


def test_every_skip_has_a_channel_and_reason(project, fake_client, monkeypatch, two_channels):
    fake_client.iter_errors[222] = ChannelPrivateError(request=None)
    run_main(monkeypatch, project, fake_client, WINDOW, "@good\n@bad\n")
    for entry in load_digest(project)["meta"]["skipped"]:
        assert entry["channel"] and entry["reason"]


def test_invalid_entry_is_reported_not_dropped(project, fake_client, monkeypatch):
    good = make_channel(111, "Good", username="good")
    fake_client.add_channel(good, [make_message(1, BASE, text="ok")])
    run_main(monkeypatch, project, fake_client, WINDOW, "@good\n???\n")
    digest = load_digest(project)
    assert digest["meta"]["channels_ok"] == 1
    assert any(s["reason"] == "invalid_entry" for s in digest["meta"]["skipped"])
