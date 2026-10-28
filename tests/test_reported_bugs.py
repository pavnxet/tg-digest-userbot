"""Regression tests for the findings in BUG_REPORT.md (2026-10-27).

Each test asserts the **correct** behaviour. They were delivered in PR #1 as
``xfail(strict=True)`` reproductions; every finding has since been fixed in
main, so the markers are gone and these now guard against a regression.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path


from conftest import run_main
from fake_bot_client import FakeEvent, build_app

OWNER = 555

BUG1 = "BUG-01 /add duplicates the label"
BUG2 = "BUG-02 --per-channel-files overwrites channels"
BUG3 = "BUG-03 /find never matches .jsonl digests"
BUG4 = "BUG-04 /files miscounts .jsonl messages"
BUG5 = "BUG-05 autodelete_minutes is a no-op"
BUG6 = "BUG-06 /preview consumes the 5-minute run slot"
BUG7 = "BUG-07 a resume button can target a different channel"
BUG8 = "BUG-08 browsing /channels aborts a login in progress"
BUG9 = "BUG-09 a run where every channel failed still advances --since-last-run"
BUG10 = "BUG-10 a channel edit silently drops tags"


def _make_script(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_main.py"
    script.write_text(body, encoding="utf-8")
    return script


def _jsonl_digest(directory: Path) -> Path:
    from formatter import build_digest, to_jsonl

    directory.mkdir(parents=True, exist_ok=True)
    digest = build_digest(
        "prompt",
        {"messages_total": 1},
        [{
            "name": "News",
            "link_base": "https://t.me/news/",
            "count": 1,
            "messages": [{"id": 7, "t": "2026-10-27 09:00", "text": "admit card released"}],
        }],
    )
    path = directory / "digest_2026-10-27_0900.jsonl"
    path.write_text(to_jsonl(digest), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# BUG-01 — /add writes the label twice
# ---------------------------------------------------------------------------
def test_add_with_a_label_stores_the_label_once(tmp_path):
    from channel_store import ChannelStore

    path = tmp_path / "channels.txt"
    path.write_text("@existing\n", encoding="utf-8")
    store = ChannelStore(path, tmp_path / "channels.lock")

    assert store.add("@news | Daily News").ok
    assert path.read_text(encoding="utf-8").splitlines()[-1] == "@news | Daily News"
    assert store.entries()[-1].label == "Daily News"


def test_add_with_an_off_marker_writes_it_once(tmp_path):
    from channel_store import ChannelStore

    path = tmp_path / "channels.txt"
    path.write_text("", encoding="utf-8")
    store = ChannelStore(path, tmp_path / "channels.lock")

    assert store.add("@news | off").ok
    assert path.read_text(encoding="utf-8").strip() == "@news | off"
    assert store.entries()[0].paused is True


# ---------------------------------------------------------------------------
# BUG-02 — one file per channel, but Devanagari titles all sanitise to "channel"
# ---------------------------------------------------------------------------
def test_per_channel_files_do_not_overwrite_each_other(tmp_path):
    import main
    from timeutil import get_tz

    out = tmp_path / "output"
    args = main.build_parser().parse_args(
        ["--today", "--output-dir", str(out), "--per-channel-files"]
    )
    channels = [
        {"name": "समाचार", "type": "channel", "link_base": "https://t.me/a/", "count": 1,
         "messages": [{"id": 1, "t": "2026-10-27 09:00", "text": "A"}]},
        {"name": "नमस्ते", "type": "channel", "link_base": "https://t.me/b/", "count": 1,
         "messages": [{"id": 2, "t": "2026-10-27 09:01", "text": "B"}]},
    ]

    written = main.write_outputs(args, "prompt", {"channels_ok": 2}, channels, get_tz())

    assert len({path.name for path, _text in written}) == 2          # distinct names
    assert len(list(out.glob("*.json"))) == 2                        # two files on disk
    names = {channel["name"] for path in out.glob("*.json")
             for channel in json.loads(path.read_text(encoding="utf-8"))["channels"]}
    assert names == {"समाचार", "नमस्ते"}                              # nothing lost


# ---------------------------------------------------------------------------
# BUG-03 / BUG-04 — the .jsonl output format
# ---------------------------------------------------------------------------
def test_find_searches_jsonl_digests(tmp_path):
    from files_store import find

    _jsonl_digest(tmp_path / "output")

    assert len(find("admit", tmp_path / "output")) == 1


def test_files_panel_counts_jsonl_messages(tmp_path):
    from files_store import _message_count

    path = _jsonl_digest(tmp_path / "output")

    assert _message_count(path) == 1


# ---------------------------------------------------------------------------
# BUG-05 — autodelete_minutes is documented but never deletes anything
# ---------------------------------------------------------------------------
def test_autodelete_removes_the_digest_it_sent(tmp_path, monkeypatch):
    import bot_run

    out = tmp_path / "output"
    out.mkdir()
    digest = out / "digest_2026-10-27_0900.json"
    digest.write_text(
        json.dumps({"meta": {"channels_ok": 1, "messages_total": 1}}), encoding="utf-8"
    )
    app, bot, _user, _owners, _session = build_app(
        tmp_path, channels_text="@a\n", run_script=_make_script(tmp_path, ""),
        output_dir=out, cache_dir=tmp_path / "cache", min_interval=0,
    )
    app._run_panel.settings.set("autodelete_minutes", "1")

    class _ImmediateAsyncio:
        """asyncio with an instant sleep, so a 1-minute delay is testable."""

        def __getattr__(self, name):
            return getattr(asyncio, name)

        async def sleep(self, _seconds):
            return None

    monkeypatch.setattr(bot_run, "asyncio", _ImmediateAsyncio())

    async def scenario():
        panel = app._run_panel
        panel._last_outputs = [digest]      # the run that just finished wrote this
        await panel._send_outputs(OWNER)
        assert panel._autodelete_task is not None
        await panel._autodelete_task

    asyncio.run(scenario())

    assert bot.files, "the digest should have been sent first"
    assert bot.deleted, "the sent digest should have been deleted again"


# ---------------------------------------------------------------------------
# BUG-06 — a dry run is charged against the 5-minute / 12-a-day run limits
# ---------------------------------------------------------------------------
def test_preview_does_not_use_up_the_run_slot(tmp_path):
    script = _make_script(tmp_path, "print('  OK    News  (~12 message(s) in window)')\n")
    app, bot, _user, _owners, _session = build_app(
        tmp_path, channels_text="@a\n", run_script=script,
        output_dir=tmp_path / "output", cache_dir=tmp_path / "cache", min_interval=300,
    )

    async def scenario():
        await app.handle(FakeEvent(bot, "/preview 24h", OWNER))

    asyncio.run(scenario())

    assert app._run_panel.rm.check_limits() is None      # /run must still be allowed
    assert app._run_panel.rm.history.last() is None      # a preview is not a "run"


# ---------------------------------------------------------------------------
# BUG-07 — resume tokens point at a shared, overwritten channels file
# ---------------------------------------------------------------------------
def test_history_resume_points_at_the_channel_it_was_created_for(tmp_path):
    script = _make_script(tmp_path, "import sys\nsys.exit(1)\n")
    app, bot, _user, _owners, _session = build_app(
        tmp_path, channels_text="@channelA\n@channelB\n", run_script=script,
        output_dir=tmp_path / "output", cache_dir=tmp_path / "cache", min_interval=0,
    )

    async def scenario():
        panel = app._run_panel
        await panel.cmd_history(FakeEvent(bot, "/history @channelA", OWNER))
        await panel._task
        argv = next(iter(panel._resume.values()))[0]
        subset = Path(argv[argv.index("--channels-file") + 1])
        assert subset.read_text(encoding="utf-8").strip() == "@channelA"

        # The owner starts a second history run for another channel.
        await panel.cmd_history(FakeEvent(bot, "/history @channelB", OWNER))
        await panel._task

        # The first run's Resume button must still mean @channelA.
        assert subset.read_text(encoding="utf-8").strip() == "@channelA"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# BUG-08 — an unrelated command tears down the user client mid-login
# ---------------------------------------------------------------------------
def test_browsing_channels_does_not_abort_a_login_in_progress(tmp_path):
    app, bot, _user, _owners, _session = build_app(tmp_path, channels_text="@somechannel\n")

    async def scenario():
        await app.handle(FakeEvent(bot, "/login_phone", OWNER))
        await app.handle(FakeEvent(bot, "/channels", OWNER))
        phone = FakeEvent(bot, "+919812345678", OWNER)
        await app.handle(phone)
        return phone

    phone = asyncio.run(scenario())
    replies = " ".join(text for text, _buttons in phone.replies)

    assert "Code request fail" not in replies
    assert app.manager.state.awaiting == "code"


# ---------------------------------------------------------------------------
# BUG-09 — --since-last-run loses a window when everything failed
# ---------------------------------------------------------------------------
def test_a_run_where_every_channel_failed_does_not_advance_since_last_run(
    project, fake_client, monkeypatch
):
    import state

    run_main(monkeypatch, project, fake_client, ["--today"], "@missing_channel\n")

    assert state.get_last_run(project["state"]) is None


# ---------------------------------------------------------------------------
# BUG-10 — the store's line parser disagrees with the digest's line parser
# ---------------------------------------------------------------------------
def test_channel_edits_keep_every_tag(tmp_path):
    from channel_store import ChannelStore
    from channels import parse_channel_line

    path = tmp_path / "channels.txt"
    path.write_text("@news | tags=jobs,delhi | tags=urgent\n", encoding="utf-8")
    expected = set(parse_channel_line(path.read_text(encoding="utf-8").strip()).tags)

    store = ChannelStore(path, tmp_path / "channels.lock")
    store.set_paused(1, True)          # any rewrite of the line

    assert set(store.entries()[0].tags) == expected
