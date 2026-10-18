"""Phase 4: end-to-end pipeline with a fake Telethon client."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from conftest import run_main
from fake_client import make_channel, make_message, make_service
from timeutil import get_tz

IST = get_tz()
BASE = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)  # 17:30 IST


def ist(dt):
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M")


def window(span_min=10):
    return ["--since", ist(BASE - timedelta(minutes=span_min)), "--until", ist(BASE + timedelta(minutes=1))]


def history(n, text="msg"):
    # newest first, id descending with time
    return [make_message(n - i, BASE - i * timedelta(minutes=1), text=f"{text} {n - i}") for i in range(n)]


def load_digest(project):
    import json

    files = sorted(project["out"].glob("*.json"))
    assert files, "no output file written"
    return json.loads(files[0].read_text(encoding="utf-8"))


def test_early_break_does_not_pull_full_history(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, history(1000))
    rc = run_main(monkeypatch, project, fake_client, window(10) + ["--format", "json"], "@news\n")
    assert rc == 0
    # 11 in-window messages + 1 that triggers the break; nowhere near 1000.
    assert fake_client.iter_yielded <= 13
    digest = load_digest(project)
    assert digest["channels"][0]["count"] == 11


def test_output_sorted_oldest_to_newest(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, history(20))
    run_main(monkeypatch, project, fake_client, window(30), "@news\n")
    ids = [m["id"] for m in load_digest(project)["channels"][0]["messages"]]
    assert ids == sorted(ids)


def test_equal_timestamps_keep_stable_order(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    msgs = [make_message(30, BASE, text="a"), make_message(20, BASE, text="b"), make_message(10, BASE, text="c")]
    fake_client.add_channel(ch, msgs)
    run_main(monkeypatch, project, fake_client, window(5), "@news\n")
    ids = [m["id"] for m in load_digest(project)["channels"][0]["messages"]]
    assert ids == [10, 20, 30]


def test_empty_channel_and_empty_window(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "Empty", username="empty")
    fake_client.add_channel(ch, [])
    run_main(monkeypatch, project, fake_client, window(5), "@empty\n")
    digest = load_digest(project)
    assert digest["meta"]["messages_total"] == 0
    assert digest["meta"]["channels_ok"] == 1
    assert digest["channels"][0]["count"] == 0


def test_window_with_no_matching_messages(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "Old", username="old")
    fake_client.add_channel(ch, history(5))  # all ~now
    run_main(monkeypatch, project, fake_client, ["--since", "2020-01-01 00:00", "--until", "2020-01-02 00:00"], "@old\n")
    assert load_digest(project)["meta"]["messages_total"] == 0


def test_service_messages_are_skipped(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, [make_message(2, BASE, text="real"), make_service(1, BASE - timedelta(minutes=1))])
    run_main(monkeypatch, project, fake_client, window(5), "@news\n")
    msgs = load_digest(project)["channels"][0]["messages"]
    assert [m["id"] for m in msgs] == [2]


def test_interval_boundaries_since_inclusive_until_exclusive(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, [
        make_message(3, BASE + timedelta(minutes=1), text="at until"),      # == until -> excluded
        make_message(2, BASE - timedelta(minutes=5), text="middle"),        # inside
        make_message(1, BASE - timedelta(minutes=10), text="at since"),     # == since -> included
    ])
    run_main(monkeypatch, project, fake_client, window(10), "@news\n")
    ids = [m["id"] for m in load_digest(project)["channels"][0]["messages"]]
    assert 1 in ids and 2 in ids      # start is inclusive
    assert 3 not in ids               # end is exclusive


# --------------------------------------------------------------------------
# Membership / joins
# --------------------------------------------------------------------------
def test_private_channel_not_joined_is_skipped(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "Private")  # no username
    fake_client.add_channel(ch, history(3))
    run_main(monkeypatch, project, fake_client, window(5), "1234567890\n")
    digest = load_digest(project)
    assert digest["meta"]["channels_ok"] == 0
    assert digest["meta"]["skipped"][0]["reason"] == "not_joined"


def test_private_channel_joined_is_read(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "Private")
    fake_client.add_channel(ch, history(3))
    fake_client.members.add(1234567890)
    run_main(monkeypatch, project, fake_client, window(5), "1234567890\n")
    assert load_digest(project)["meta"]["channels_ok"] == 1


def test_invite_not_joined_skipped_without_auto_join(project, fake_client, monkeypatch):
    fake_client.invites["AbCdEf"] = SimpleNamespace(request_needed=False)
    run_main(monkeypatch, project, fake_client, window(5), "https://t.me/+AbCdEf\n")
    assert load_digest(project)["meta"]["skipped"][0]["reason"] == "not_joined"


def test_invite_joined_with_auto_join(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "Invited", username="invited")
    fake_client.add_channel(ch, history(3))
    fake_client.invites["AbCdEf"] = SimpleNamespace(request_needed=False)
    fake_client.import_result = SimpleNamespace(chats=[ch])
    monkeypatch.setattr("channels.asyncio.sleep", _no_sleep)
    run_main(monkeypatch, project, fake_client, window(5) + ["--auto-join"], "https://t.me/+AbCdEf\n")
    assert load_digest(project)["meta"]["channels_ok"] == 1


def test_invite_pending_approval(project, fake_client, monkeypatch):
    fake_client.invites["AbCdEf"] = SimpleNamespace(request_needed=True)
    run_main(monkeypatch, project, fake_client, window(5) + ["--auto-join"], "https://t.me/+AbCdEf\n")
    assert load_digest(project)["meta"]["skipped"][0]["reason"] == "pending_approval"


def test_max_joins_enforced(project, fake_client, monkeypatch):
    ch1 = make_channel(111, "A", username="a")
    ch2 = make_channel(222, "B", username="b")
    fake_client.add_channel(ch1, history(2))
    fake_client.add_channel(ch2, history(2))
    fake_client.invites["H1"] = SimpleNamespace(request_needed=False)
    fake_client.invites["H2"] = SimpleNamespace(request_needed=False)
    fake_client.import_result = SimpleNamespace(chats=[ch1])
    monkeypatch.setattr("channels.asyncio.sleep", _no_sleep)
    run_main(monkeypatch, project, fake_client, window(5) + ["--auto-join", "--max-joins", "1"],
             "https://t.me/+H1\nhttps://t.me/+H2\n")
    reasons = [s["reason"] for s in load_digest(project)["meta"]["skipped"]]
    assert "max_joins" in reasons


def test_join_delay_is_in_30_60_range(project, fake_client, monkeypatch):
    sleeps = []

    async def _record_sleep(seconds):
        sleeps.append(seconds)

    ch1 = make_channel(111, "A", username="a")
    ch2 = make_channel(222, "B", username="b")
    fake_client.add_channel(ch1, history(2))
    fake_client.add_channel(ch2, history(2))
    fake_client.invites["H1"] = SimpleNamespace(request_needed=False)
    fake_client.invites["H2"] = SimpleNamespace(request_needed=False)
    fake_client.import_result = SimpleNamespace(chats=[ch1])
    monkeypatch.setattr("channels.asyncio.sleep", _record_sleep)
    run_main(monkeypatch, project, fake_client, window(5) + ["--auto-join"],
             "https://t.me/+H1\nhttps://t.me/+H2\n")
    join_waits = [s for s in sleeps if s >= 30]
    assert join_waits, "no join delay observed"
    assert all(30 <= s <= 60 for s in join_waits)


# --------------------------------------------------------------------------
# Caps, counts, dry-run, no-write guarantee
# --------------------------------------------------------------------------
def test_max_per_channel_cap_is_respected(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, history(100))
    run_main(monkeypatch, project, fake_client, window(90) + ["--max-per-channel", "10"], "@news\n")
    assert load_digest(project)["channels"][0]["count"] == 10


def test_truncation_is_visible_in_meta(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, history(100))
    run_main(monkeypatch, project, fake_client, window(90) + ["--max-per-channel", "10"], "@news\n")
    digest = load_digest(project)
    assert digest["channels"][0]["truncated"] is True
    assert digest["channels"][0]["cap"] == 10
    assert digest["meta"]["truncated_channels"] == ["News"]


def test_no_truncation_fields_when_nothing_truncated(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, history(5))
    run_main(monkeypatch, project, fake_client, window(30), "@news\n")
    digest = load_digest(project)
    assert "truncated" not in digest["channels"][0]
    assert "truncated_channels" not in digest["meta"]


def test_counts_are_consistent(project, fake_client, monkeypatch):
    ch1 = make_channel(111, "A", username="a")
    ch2 = make_channel(222, "B", username="b")
    fake_client.add_channel(ch1, history(7))
    fake_client.add_channel(ch2, history(4))
    run_main(monkeypatch, project, fake_client, window(30), "@a\n@b\n")
    digest = load_digest(project)
    total = sum(c["count"] for c in digest["channels"])
    assert digest["meta"]["messages_total"] == total
    assert digest["meta"]["channels_ok"] == len(digest["channels"])
    for c in digest["channels"]:
        assert c["count"] == len(c.get("messages", []))


def test_dry_run_writes_nothing_and_leaves_state(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, history(5))
    run_main(monkeypatch, project, fake_client, window(5) + ["--dry-run"], "@news\n")
    assert list(project["out"].glob("*")) == []
    assert not project["state"].exists()


def test_no_forbidden_methods_called(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, history(5))
    run_main(monkeypatch, project, fake_client, window(5), "@news\n")
    forbidden = [c for c in fake_client.calls if str(c[0]).startswith("FORBIDDEN")]
    assert forbidden == []


# --------------------------------------------------------------------------
# BUG-01 / BUG-02
# --------------------------------------------------------------------------
def test_duplicate_channel_forms_produce_one_entry(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, history(5))
    run_main(monkeypatch, project, fake_client, window(5), "https://t.me/news\n@news\n")
    digest = load_digest(project)
    assert len(digest["channels"]) == 1
    assert digest["meta"]["channels_ok"] == 1
    assert digest["meta"].get("skipped", []) == []


def test_unexpected_error_in_one_channel_does_not_abort_run(project, fake_client, monkeypatch):
    good = make_channel(111, "Good", username="good")
    bad = make_channel(222, "Bad", username="bad")
    fake_client.add_channel(good, history(3))
    fake_client.add_channel(bad, history(3))
    fake_client.iter_errors[222] = RuntimeError("boom")
    run_main(monkeypatch, project, fake_client, window(5), "@good\n@bad\n")
    digest = load_digest(project)
    assert digest["meta"]["channels_ok"] == 1
    assert any(s["reason"] == "unexpected_error:RuntimeError" for s in digest["meta"].get("skipped", []))


# --------------------------------------------------------------------------
# KeyboardInterrupt
# --------------------------------------------------------------------------
def test_since_last_run_future_falls_back_to_24h(project, fake_client, monkeypatch, capsys):
    import json as _json

    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, history(5))
    future = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    project["state"].write_text(_json.dumps({"last_run_utc": future}), encoding="utf-8")
    rc = run_main(monkeypatch, project, fake_client, ["--since-last-run"], "@news\n")
    assert rc == 0
    assert "falling back to --last 24h" in capsys.readouterr().out
    assert load_digest(project)["meta"]["messages_total"] == 5


def test_since_last_run_corrupt_state_falls_back_to_24h(project, fake_client, monkeypatch, capsys):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, history(5))
    project["state"].write_text("{ not json", encoding="utf-8")
    rc = run_main(monkeypatch, project, fake_client, ["--since-last-run"], "@news\n")
    assert rc == 0
    assert "falling back to --last 24h" in capsys.readouterr().out


def test_since_last_run_valid_state_is_used(project, fake_client, monkeypatch):
    import json as _json

    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, history(5))
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    project["state"].write_text(_json.dumps({"last_run_utc": recent}), encoding="utf-8")
    rc = run_main(monkeypatch, project, fake_client, ["--since-last-run"], "@news\n")
    assert rc == 0


# --------------------------------------------------------------------------
# KeyboardInterrupt
# --------------------------------------------------------------------------
def test_keyboard_interrupt_leaves_state_untouched(project, fake_client, monkeypatch):
    ch1 = make_channel(111, "A", username="a")
    ch2 = make_channel(222, "B", username="b")
    fake_client.add_channel(ch1, history(3))
    fake_client.add_channel(ch2, history(3))
    fake_client.iter_errors[222] = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run_main(monkeypatch, project, fake_client, window(5), "@a\n@b\n")
    assert not project["state"].exists()
    # checkpoint for the completed first channel must exist
    checkpoints = list(project["cache"].glob("*.jsonl"))
    assert checkpoints and "A" in checkpoints[0].read_text(encoding="utf-8")


async def _no_sleep(seconds):
    return None
