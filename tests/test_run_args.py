"""Section C/E: /run argument parsing and settings validation."""
import pytest

from run_args import ArgError, build_argv, parse_window
from settings_store import DEFAULTS, SettingsStore


class _Settings:
    def as_run_flags(self):
        return ["--format", "json"]


@pytest.fixture
def settings():
    return _Settings()


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("today", ["--today"]),
    ("yesterday", ["--yesterday"]),
    ("6h", ["--last", "6h"]),
    ("24h", ["--last", "24h"]),
    ("3d", ["--last", "3d"]),
    ("since-last", ["--since-last-run"]),
])
def test_simple_windows(text, expected):
    argv, _ = parse_window(text)
    assert argv == expected


def test_explicit_window():
    argv, rest = parse_window("2026-10-17 06:00 2026-10-18 18:00 extra")
    assert argv == ["--since", "2026-10-17 06:00", "--until", "2026-10-18 18:00"]
    assert rest.strip() == "extra"


def test_bad_window():
    with pytest.raises(ArgError):
        parse_window("lastweek")
    with pytest.raises(ArgError):
        parse_window("")
    with pytest.raises(ArgError):
        parse_window("2026-13-99 06:00 2026-10-18 18:00")


# --------------------------------------------------------------------------
# Options + injection
# --------------------------------------------------------------------------
def test_options_build_argv(settings):
    argv, send = build_argv("today dedupe views min:20 send:no", settings)
    assert "--dedupe" in argv and "--with-views" in argv
    assert "--min-chars" in argv and argv[argv.index("--min-chars") + 1] == "20"
    assert send is False


@pytest.mark.parametrize("bad", [
    "today --auto-join",
    "today; rm -rf /",
    "today && echo pwned",
    "today\ntoday",
    "today min:9999",
    "today send:maybe",
    "today @#$%",
])
def test_injection_is_rejected_and_autojoin_never_reaches_argv(bad, settings):
    with pytest.raises(ArgError):
        build_argv(bad, settings)
    # even if it were accepted, --auto-join must never be in the argv
    try:
        argv, _ = build_argv(bad, settings)
        assert "--auto-join" not in argv
    except ArgError:
        pass


def test_min_bounds(settings):
    with pytest.raises(ArgError):
        build_argv("today min:-1", settings)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
def test_settings_defaults_and_set(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    assert store.get("format") == "json"
    ok, _ = store.set("min_chars", "25")
    assert ok and store.get("min_chars") == 25
    assert SettingsStore(tmp_path / "settings.json").get("min_chars") == 25


@pytest.mark.parametrize("key,value", [
    ("format", "xml"),
    ("dedupe", "maybe"),
    ("min_chars", "9999"),
    ("max_chars", "10"),
    ("retention_days", "0"),
    ("max_per_channel", "5"),
    ("nope", "1"),
])
def test_settings_reject_invalid(tmp_path, key, value):
    store = SettingsStore(tmp_path / "settings.json")
    ok, message = store.set(key, value)
    assert ok is False and message
    assert store.get(key) == DEFAULTS.get(key)


def test_settings_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    assert SettingsStore(path).get("format") == "json"


def test_as_run_flags(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.set("dedupe", "on")
    store.set("with_views", "on")
    flags = store.as_run_flags()
    assert "--dedupe" in flags and "--with-views" in flags


# --------------------------------------------------------------------------
# The window may appear anywhere (people type it both ways)
# --------------------------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("3d channel:5", (["--last", "3d"], "channel:5")),
    ("channel:5 3d", (["--last", "3d"], "channel:5")),
    ("today", (["--today"], "")),
    ("group:news 24h dedupe", (["--last", "24h"], "group:news dedupe")),
    ("all", (["--all-history"], "")),
    ("dedupe since-last", (["--since-last-run"], "dedupe")),
])
def test_parse_window_accepts_the_window_anywhere(text, expected):
    assert parse_window(text) == expected


def test_parse_window_accepts_a_date_range_anywhere():
    argv, rest = parse_window("channel:5 2026-10-17 06:00 2026-10-18 18:00")
    assert argv == ["--since", "2026-10-17 06:00", "--until", "2026-10-18 18:00"]
    assert rest == "channel:5"


def test_the_reported_failing_command_now_works(settings):
    def writer(token):
        return "/tmp/subset.txt", None

    argv, _send = build_argv("channel:5 3d", settings, subset_writer=writer)
    assert "--last" in argv and "3d" in argv
    assert "--channels-file" in argv


def test_missing_window_error_is_actionable():
    with pytest.raises(ArgError) as exc:
        parse_window("channel:5")
    message = str(exc.value)
    assert "window" in message
    assert "उदाहरण" in message        # shows a working example
    assert "3d" in message            # lists the valid windows
