"""Full-history scraping: the `all` window, /history, and its longer timeout."""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from config import (
    DEFAULT_MAX_FLOOD_WAIT,
    DEFAULT_MAX_PER_CHANNEL,
    HISTORY_MAX_FLOOD_WAIT,
    HISTORY_TIMEOUT_SECONDS,
)
from fake_bot_client import FakeEvent, build_app
from run_args import ArgError, build_argv, parse_window
from settings_store import SettingsStore
from timeutil import ALL_HISTORY_SINCE, resolve_window

OWNER = 555


def _settings(tmp_path):
    return SettingsStore(tmp_path / "settings.json")


# --------------------------------------------------------------------------
# The window
# --------------------------------------------------------------------------
def test_all_history_window_spans_from_the_epoch_to_now():
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    window = resolve_window(all_history=True, now=now)
    assert window.since == ALL_HISTORY_SINCE
    assert window.until == now
    # A message from 2015 is inside it -- nothing is excluded.
    assert window.contains(datetime(2015, 1, 1, tzinfo=timezone.utc))


def test_all_history_is_mutually_exclusive_with_other_windows():
    with pytest.raises(ValueError):
        resolve_window(all_history=True, today=True)
    with pytest.raises(ValueError):
        resolve_window(all_history=True, last="24h")
    with pytest.raises(ValueError):
        resolve_window(all_history=True, since="2026-01-01 00:00")


# --------------------------------------------------------------------------
# argv building
# --------------------------------------------------------------------------
def test_all_token_becomes_all_history():
    argv, rest = parse_window("all")
    assert argv == ["--all-history"]
    assert rest == ""


def test_all_history_removes_the_settings_message_cap(tmp_path):
    argv, send_files = build_argv("all", _settings(tmp_path))
    assert "--all-history" in argv
    index = argv.index("--max-per-channel")
    assert argv[index + 1] == "0"          # the settings value is overridden
    assert send_files is False             # a huge history is not sent unasked


def test_all_history_can_still_send_the_file_when_asked(tmp_path):
    _argv, send_files = build_argv("all send:yes", _settings(tmp_path))
    assert send_files is True


def test_resume_token_is_accepted(tmp_path):
    argv, _ = build_argv("today resume", _settings(tmp_path))
    assert "--resume" in argv


@pytest.mark.parametrize("text", [
    "all --auto-join",
    "all; rm -rf /",
    "all && whoami",
    "all\n--auto-join",
    "all $(id)",
    "all `id`",
    "all | cat",
])
def test_history_arguments_cannot_inject_flags(tmp_path, text):
    with pytest.raises(ArgError):
        build_argv(text, _settings(tmp_path))


def test_auto_join_can_never_reach_argv_even_with_all_history(tmp_path):
    for text in ("all", "all send:yes", "all dedupe"):
        argv, _ = build_argv(text, _settings(tmp_path))
        assert "--auto-join" not in argv


# --------------------------------------------------------------------------
# main.py defaults
# --------------------------------------------------------------------------
def test_parser_exposes_all_history_and_defers_the_caps():
    import main

    parser = main.build_parser()
    args = parser.parse_args(["--all-history"])
    assert args.all_history is True
    assert args.max_per_channel is None    # run() fills this in
    assert args.max_flood_wait is None
    assert parser.parse_args(["--today"]).all_history is False


def test_mode_defaults_remove_the_cap_only_for_full_history():
    import main

    parser = main.build_parser()

    normal = parser.parse_args(["--today"])
    main.apply_mode_defaults(normal)
    assert normal.max_per_channel == DEFAULT_MAX_PER_CHANNEL
    assert normal.max_flood_wait == DEFAULT_MAX_FLOOD_WAIT

    history = parser.parse_args(["--all-history"])
    main.apply_mode_defaults(history)
    assert history.max_per_channel == 0            # no cap
    assert history.max_flood_wait == HISTORY_MAX_FLOOD_WAIT


def test_explicit_cap_still_wins_over_the_full_history_default():
    import main

    args = main.build_parser().parse_args(["--all-history", "--max-per-channel", "700"])
    main.apply_mode_defaults(args)
    assert args.max_per_channel == 700


# --------------------------------------------------------------------------
# RunManager timeout override
# --------------------------------------------------------------------------
def test_run_manager_honours_a_per_run_timeout(tmp_path):
    from run_manager import RunManager

    script = tmp_path / "m.py"
    script.write_text("import time; time.sleep(0.2)\n", encoding="utf-8")
    rm = RunManager(
        main_path=script, lock_path=tmp_path / "run.lock", history_path=tmp_path / "h.json",
        python=sys.executable, timeout=30, min_interval=0, max_per_day=100,
    )

    async def scenario():
        assert await rm.start([], timeout=HISTORY_TIMEOUT_SECONDS) is None
        assert rm._run_timeout == HISTORY_TIMEOUT_SECONDS
        await rm.wait()

    asyncio.run(scenario())
    assert rm._run_timeout == HISTORY_TIMEOUT_SECONDS


# --------------------------------------------------------------------------
# /history in the bot
# --------------------------------------------------------------------------
def _panel(tmp_path, channels_text):
    """Wire the bot with a fake main.py and capture what /history starts."""
    script = tmp_path / "fake_main.py"
    script.write_text("pass\n", encoding="utf-8")
    app, bot, _user, _owners, _session = build_app(
        tmp_path, channels_text=channels_text, run_script=script,
    )
    calls: list[dict] = []

    async def fake_start(argv, capture=False, timeout=None):
        calls.append({"argv": list(argv), "timeout": timeout})
        return None

    async def fake_supervise(chat_id, send_files):
        return None

    app._run_panel.rm.start = fake_start
    app._run_panel._supervise = fake_supervise
    return app, bot, calls


def test_history_resolves_by_name_and_uses_all_history(tmp_path):
    app, bot, calls = _panel(tmp_path, "@news | News\n@jobs | Jobs\n")
    asyncio.run(app.handle(FakeEvent(bot, "/history News", OWNER)))

    assert calls, "no run was started"
    argv = calls[0]["argv"]
    assert "--all-history" in argv
    assert "--channels-file" in argv
    subset = Path(argv[argv.index("--channels-file") + 1])
    assert subset.read_text(encoding="utf-8").strip() == "@news | News"
    assert calls[0]["timeout"] == HISTORY_TIMEOUT_SECONDS


def test_history_resolves_by_number(tmp_path):
    app, bot, calls = _panel(tmp_path, "@news | News\n@jobs | Jobs\n")
    asyncio.run(app.handle(FakeEvent(bot, "/history 2", OWNER)))
    argv = calls[0]["argv"]
    subset = Path(argv[argv.index("--channels-file") + 1])
    assert subset.read_text(encoding="utf-8").strip() == "@jobs | Jobs"


def test_history_passes_extra_options_through(tmp_path):
    app, bot, calls = _panel(tmp_path, "@news | News\n")
    asyncio.run(app.handle(FakeEvent(bot, "/history 1 dedupe min:40 send:yes", OWNER)))
    argv = calls[0]["argv"]
    assert "--dedupe" in argv and "--min-chars" in argv


def test_history_ambiguous_name_asks_for_a_number(tmp_path):
    app, bot, calls = _panel(tmp_path, "@a | News\n@b | News\n")
    asyncio.run(app.handle(FakeEvent(bot, "/history News", OWNER)))
    assert not calls, "an ambiguous match must not start a run"
    assert "कई चैनलों" in bot.all_text()


def test_history_unknown_channel_does_not_run(tmp_path):
    app, bot, calls = _panel(tmp_path, "@news | News\n")
    asyncio.run(app.handle(FakeEvent(bot, "/history nope", OWNER)))
    assert not calls
    assert "कोई चैनल नहीं मिला" in bot.all_text()


def test_history_rejects_channel_filters(tmp_path):
    app, bot, calls = _panel(tmp_path, "@news | News\n")
    asyncio.run(app.handle(FakeEvent(bot, "/history 1 channel:2", OWNER)))
    assert not calls
    assert "group:/channel:" in bot.all_text()


def test_history_without_arguments_shows_the_guide(tmp_path):
    app, bot, calls = _panel(tmp_path, "@news | News\n")
    event = FakeEvent(bot, "/history", OWNER)
    asyncio.run(app.handle(event))
    assert not calls
    assert "Usage" in bot.all_text()
    assert event.parse_modes[-1] == "html"


def test_history_is_owner_only(tmp_path):
    app, bot, calls = _panel(tmp_path, "@news | News\n")
    asyncio.run(app.handle(FakeEvent(bot, "/history News", OWNER + 1)))
    assert not calls
    assert not bot.sent, "a non-owner must get no reply at all"


# --------------------------------------------------------------------------
# The progress line must count the run's subset, not the whole channels.txt
# --------------------------------------------------------------------------
def test_history_progress_counts_only_the_one_channel(tmp_path):
    app, bot, _calls = _panel(tmp_path, "@news | News\n@jobs | Jobs\n@a | A\n@b | B\n")
    asyncio.run(app.handle(FakeEvent(bot, "/history 2", OWNER)))
    assert app._run_panel._total_channels() == 1


def test_group_run_progress_counts_only_the_group(tmp_path):
    app, bot, _calls = _panel(
        tmp_path, "@a | A | tags=news\n@b | B | tags=news\n@c | C | tags=jobs\n",
    )
    asyncio.run(app.handle(FakeEvent(bot, "/run today group:news", OWNER)))
    assert app._run_panel._total_channels() == 2


def test_progress_falls_back_to_the_whole_list(tmp_path):
    app, bot, _calls = _panel(tmp_path, "@a | A\n@b | B\n")
    asyncio.run(app.handle(FakeEvent(bot, "/run today", OWNER)))
    assert app._run_panel._total_channels() == 2


def test_channels_file_arg_helper():
    from bot_run import _channels_file_arg

    assert _channels_file_arg(["--all-history", "--channels-file", "/tmp/x.txt"]) == "/tmp/x.txt"
    assert _channels_file_arg(["--today"]) is None
    assert _channels_file_arg(["--channels-file"]) is None


def test_history_refuses_a_paused_channel(tmp_path):
    app, bot, calls = _panel(tmp_path, "@p | Paused | off\n@q | Q\n")
    asyncio.run(app.handle(FakeEvent(bot, "/history 1", OWNER)))
    assert not calls, "a paused channel must not start a run"
    text = bot.all_text()
    assert "paused" in text and "/resume 1" in text
