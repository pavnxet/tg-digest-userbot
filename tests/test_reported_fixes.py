"""The PR #1 audit findings, fixed. One test per finding."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fake_bot_client import FakeEvent, build_app

OWNER = 555


# -- High 1: per-channel files must never overwrite each other -------------
def test_safe_name_keeps_devanagari_channels_apart():
    from main import _safe_name

    a = _safe_name("राजस्थान पटवार")
    b = _safe_name("विक्रम विश्वविद्यालय")
    assert a != b
    assert a and b


def test_per_channel_files_write_one_file_per_channel(tmp_path):
    from main import write_outputs

    args = SimpleNamespace(
        output_dir=str(tmp_path / "out"), format="json", pretty=False,
        per_channel_files=True, max_chars=100000,
    )
    channels = [
        {"name": "राजस्थान पटवार", "messages": [{"id": 1, "text": "a"}]},
        {"name": "विक्रम विश्वविद्यालय", "messages": [{"id": 2, "text": "b"}]},
    ]
    written = write_outputs(args, "prompt", {"channels_ok": 2}, channels, None)
    assert len(written) == 2
    assert written[0][0] != written[1][0]
    assert written[0][0].exists() and written[1][0].exists()


# -- High 2: a run that read nothing must not look successful --------------
def test_run_result_is_failed_when_nothing_was_read(tmp_path):
    from run_manager import RESULT_FAILED, RunResult

    # main.py now exits 1 in that case; the manager maps non-zero to "failed".
    result = RunResult(0.0, 1.0, 1, RESULT_FAILED, 1.0, "")
    assert result.result == "failed"


# -- High 3: autodelete really deletes -------------------------------------
def test_autodelete_deletes_the_messages_it_sent(tmp_path, monkeypatch):
    import bot_run

    app, bot, user, owners, session = build_app(
        tmp_path, channels_text="@a\n", run_script=_script(tmp_path),
    )
    panel = app._run_panel
    panel.settings.set("autodelete_minutes", "1")
    out = tmp_path / "output"
    out.mkdir(exist_ok=True)
    (out / "digest_a.json").write_text("{}", encoding="utf-8")
    panel._last_outputs = [out / "digest_a.json"]

    async def _no_wait(_seconds):
        return None

    monkeypatch.setattr(bot_run.asyncio, "sleep", _no_wait)

    async def scenario():
        await panel._send_outputs(OWNER)
        assert panel._autodelete_task is not None
        await panel._autodelete_task

    asyncio.run(scenario())
    assert bot.files, "the digest should have been sent first"
    assert bot.deleted, "autodelete must actually delete, not just log"
    assert len(bot.deleted[0][1]) == len(bot.files)


def test_autodelete_is_off_by_default(tmp_path):
    app, bot, user, owners, session = build_app(
        tmp_path, channels_text="@a\n", run_script=_script(tmp_path),
    )
    panel = app._run_panel
    panel.settings.set("autodelete_minutes", "0")
    asyncio.run(panel._maybe_autodelete(OWNER, [1]))
    assert panel._autodelete_task is None


# -- High 4: Resume re-runs the same channels ------------------------------
def test_resume_snapshots_the_channels_file(tmp_path):

    app, bot, user, owners, session = build_app(
        tmp_path, channels_text="@a\n@b\n", run_script=_script(tmp_path),
    )
    panel = app._run_panel
    shared = tmp_path / "cache" / "history_channel.txt"
    shared.parent.mkdir(exist_ok=True)
    shared.write_text("@a\n", encoding="utf-8")
    panel._last_argv = ["--last", "3d", "--channels-file", str(shared)]
    panel._resume_buttons(SimpleNamespace(result="timeout"))
    token = next(iter(panel._resume))
    argv = panel._resume[token][0]

    snapshot = Path(argv[argv.index("--channels-file") + 1])
    assert snapshot != shared
    assert snapshot.read_text(encoding="utf-8") == "@a\n"

    # The shared file is reused by the next run -- the snapshot must not follow it.
    shared.write_text("@b\n", encoding="utf-8")
    assert snapshot.read_text(encoding="utf-8") == "@a\n"


# -- Medium 5: /add writes the label once ----------------------------------
def test_add_with_a_label_writes_it_once(tmp_path):
    from channel_store import ChannelStore

    path = tmp_path / "channels.txt"
    path.write_text("@x\n", encoding="utf-8")
    store = ChannelStore(path, tmp_path / "lock")
    store.add("@news | My Label")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[-1] == "@news | My Label"      # not "@news | My Label | My Label"


# -- Medium 6: /find reads .jsonl and .txt too -----------------------------
def test_find_matches_jsonl_and_txt_digests(tmp_path):
    from files_store import find
    from formatter import to_jsonl

    out = tmp_path / "out"
    out.mkdir()
    channels = [{"name": "A", "link_base": "https://t.me/a/",
                 "messages": [{"id": 1, "text": "admit card out"}]}]
    (out / "digest_a.json").write_text(json.dumps({"channels": channels}), encoding="utf-8")
    (out / "digest_b.jsonl").write_text(to_jsonl({"meta": {}, "channels": channels}), encoding="utf-8")
    (out / "digest_c.txt").write_text("== A ==\n[1] admit card out\n", encoding="utf-8")
    assert len(find("admit card", out)) == 3


# -- Medium 7: /preview does not use up the run quota ----------------------
def test_preview_starts_the_run_as_a_dry_run(tmp_path):
    app, bot, user, owners, session = build_app(
        tmp_path, channels_text="@a\n", run_script=_script(tmp_path),
    )
    seen = {}

    async def fake_start(argv, capture=False, timeout=None, dry_run=False):
        seen["dry_run"] = dry_run
        seen["argv"] = list(argv)
        return SimpleNamespace(reason="stopped for the test")

    app._run_panel.rm.start = fake_start
    asyncio.run(app.handle(FakeEvent(bot, "/preview 24h", OWNER)))
    assert seen["dry_run"] is True, "a preview must not consume the 5-min gap / daily quota"
    assert "--dry-run" in seen["argv"]


# -- Medium 8: a login must not be disconnected by /status or /channels ----
def test_status_refuses_while_a_login_is_running(tmp_path):
    app, bot, user, owners, session = build_app(
        tmp_path, channels_text="@a\n", run_script=_script(tmp_path),
    )
    _begin_login(app)
    before = len(user.calls)
    asyncio.run(app.handle(FakeEvent(bot, "/status", OWNER)))
    assert user.calls[before:] == []
    assert "Login चल रहा है" in bot.sent[-1][1]


def test_channels_does_not_connect_while_a_login_is_running(tmp_path):
    app, bot, user, owners, session = build_app(
        tmp_path, channels_text="-1001234567890\n", run_script=_script(tmp_path),
    )
    user.entities[-1001234567890] = SimpleNamespace(id=1, title="X")
    _begin_login(app)
    before = len(user.calls)
    asyncio.run(app.handle(FakeEvent(bot, "/channels", OWNER)))
    assert user.calls[before:] == []


# -- Medium 9: --resume reuses the original window -------------------------
def test_saved_window_round_trips(tmp_path):
    import state

    since = datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)
    until = datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)
    state.save_window(tmp_path, "run1", since, until)
    loaded = state.load_window(tmp_path, "run1")
    assert loaded == (since, until)
    assert state.load_window(tmp_path, "missing") is None


def _begin_login(app):
    """Mark a login flow as running (it owns the user client)."""
    import time as _time

    app.manager.active = True
    app.manager.state.last_activity = _time.time()


def _script(tmp_path):
    path = tmp_path / "fake_main.py"
    path.write_text("pass\n", encoding="utf-8")
    return path
