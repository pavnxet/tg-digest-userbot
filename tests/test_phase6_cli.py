"""Phase 6: CLI argument handling, config, logs and .gitignore."""
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

import config
import main
from conftest import run_main
from fake_client import FakeTelegramClient, make_channel, make_message
from timeutil import get_tz

ROOT = Path(__file__).resolve().parent.parent
IST = get_tz()


def _now_message(msg_id=1, text="x"):
    return make_message(msg_id, datetime.now(timezone.utc), text=text)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # python-dotenv does not override pre-set env vars, so clear them first.
    for key in ("API_ID", "API_HASH", "PHONE"):
        monkeypatch.delenv(key, raising=False)


# --------------------------------------------------------------------------
# argparse
# --------------------------------------------------------------------------
def test_help_works(capsys):
    with pytest.raises(SystemExit) as exc:
        main.build_parser().parse_args(["--help"])
    assert exc.value.code == 0
    assert "usage" in capsys.readouterr().out.lower()


def test_unknown_flag_is_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        main.build_parser().parse_args(["--not-a-flag"])
    assert exc.value.code == 2


def test_bad_format_choice_is_rejected():
    with pytest.raises(SystemExit):
        main.build_parser().parse_args(["--today", "--format", "xml"])


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------
def test_missing_env_gives_friendly_error(tmp_path):
    env = tmp_path / ".env"
    env.write_text("API_ID=1\n", encoding="utf-8")  # missing API_HASH; PHONE is optional
    with pytest.raises(RuntimeError) as exc:
        config.load_credentials(env)
    message = str(exc.value)
    assert "API_HASH" in message
    assert "Traceback" not in message


def test_phone_is_optional_after_qr_login(tmp_path):
    # The QR-login .env has no PHONE; loading credentials must still succeed.
    env = tmp_path / ".env"
    env.write_text("API_ID=1\nAPI_HASH=h\nBOT_TOKEN=t\nOWNER_ID=1\n", encoding="utf-8")
    credentials = config.load_credentials(env)
    assert credentials.api_id == 1 and credentials.api_hash == "h"
    assert credentials.phone == ""


def test_login_without_phone_lets_telethon_prompt():
    import asyncio

    from client import login

    class FakeClient:
        def __init__(self):
            self.phone_kwarg = "unset"

        async def start(self, **kwargs):
            self.phone_kwarg = kwargs.get("phone", "<omitted>")

    fake = FakeClient()
    asyncio.run(login(fake, ""))
    assert fake.phone_kwarg == "<omitted>"          # no phone kwarg -> Telethon prompts
    asyncio.run(login(fake, "+911234567890"))
    assert fake.phone_kwarg == "+911234567890"


class _NoStartFake(FakeTelegramClient):
    """Mimics Telethon: start() must never be called for an authorized session."""

    def __init__(self):
        super().__init__()
        self.auth = True

    async def is_user_authorized(self):
        return self.auth

    async def start(self, *args, **kwargs):
        raise AssertionError("start() must not be called for an already-authorized session")


def test_authorized_session_never_calls_start(monkeypatch, project):
    # Regression: with a QR-login .env (no PHONE) and an authorized session,
    # main.run() must not call login()/start(), which would raise
    # "No phone number or bot token provided".
    import asyncio
    from types import SimpleNamespace

    import main

    client = _NoStartFake()
    channel = make_channel(1234567890, "News", username="news")
    client.add_channel(channel, [_now_message(1, "hello")])
    monkeypatch.setattr(main, "build_client", lambda creds, session: client)
    monkeypatch.setattr(main, "load_credentials", lambda: SimpleNamespace(api_id=1, api_hash="h", phone=""))
    project["channels"].write_text("@news\n", encoding="utf-8")
    args = main.build_parser().parse_args(["--last", "1h"])
    assert asyncio.run(main.run(args)) == 0


def test_not_logged_in_returns_2_and_never_calls_start(monkeypatch, project, capsys):
    import asyncio
    from types import SimpleNamespace

    import main

    client = _NoStartFake()
    client.auth = False
    monkeypatch.setattr(main, "build_client", lambda creds, session: client)
    monkeypatch.setattr(main, "load_credentials", lambda: SimpleNamespace(api_id=1, api_hash="h", phone=""))
    project["channels"].write_text("@news\n", encoding="utf-8")
    args = main.build_parser().parse_args(["--last", "1h"])
    assert asyncio.run(main.run(args)) == 2
    assert "Not logged in" in capsys.readouterr().out


def test_non_integer_api_id_errors(tmp_path):
    env = tmp_path / ".env"
    env.write_text("API_ID=abc\nAPI_HASH=h\nPHONE=+1\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        config.load_credentials(env)


# --------------------------------------------------------------------------
# channels.txt presence
# --------------------------------------------------------------------------
def test_missing_channels_file_is_friendly(project, fake_client, monkeypatch, capsys):
    rc = run_main(monkeypatch, project, fake_client, ["--today"], channels_text=None)
    assert rc == 1
    assert "Channels file not found" in capsys.readouterr().out


def test_empty_channels_file_is_friendly(project, fake_client, monkeypatch, capsys):
    rc = run_main(monkeypatch, project, fake_client, ["--today"], channels_text="# only a comment\n")
    assert rc == 1
    assert "No channels found" in capsys.readouterr().out


# --------------------------------------------------------------------------
# BUG-03 / BUG-06
# --------------------------------------------------------------------------
def test_two_runs_same_minute_do_not_overwrite(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, [_now_message()])
    argv = ["--last", "1h"]
    run_main(monkeypatch, project, fake_client, argv, "@news\n")
    run_main(monkeypatch, project, fake_client, argv, "@news\n")
    assert len(list(project["out"].glob("*.json"))) >= 2


def test_unwritable_output_dir_is_friendly(project, fake_client, monkeypatch, capsys):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, [_now_message()])
    blocker = project["tmp"] / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(main, "OUTPUT_DIR", blocker)
    rc = run_main(monkeypatch, project, fake_client, ["--last", "1h"], "@news\n")  # must not raise
    assert rc == 2
    assert "not writable" in capsys.readouterr().out


# --------------------------------------------------------------------------
# --summary-prompt-file
# --------------------------------------------------------------------------
def _load(project):
    import json

    return json.loads(sorted(project["out"].glob("*.json"))[0].read_text(encoding="utf-8"))


def test_empty_prompt_file_falls_back_to_default(project, fake_client, monkeypatch):
    from prompts import load_prompt

    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, [_now_message()])
    empty = project["tmp"] / "empty.txt"
    empty.write_text("   \n", encoding="utf-8")
    run_main(monkeypatch, project, fake_client,
             ["--last", "1h", "--summary-prompt-file", str(empty)], "@news\n")
    # the fallback is the file-based default prompt, not the old built-in text
    assert _load(project)["ai_instructions"] == load_prompt("default")


def test_custom_prompt_file_overrides_default(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, [_now_message()])
    prompt = project["tmp"] / "p.txt"
    prompt.write_text("Summarise briefly.", encoding="utf-8")
    run_main(monkeypatch, project, fake_client,
             ["--last", "1h", "--summary-prompt-file", str(prompt)], "@news\n")
    assert _load(project)["ai_instructions"] == "Summarise briefly."


def test_missing_summary_prompt_file_is_friendly(project, fake_client, monkeypatch, capsys):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, [_now_message()])
    rc = run_main(monkeypatch, project, fake_client,
                  ["--last", "1h", "--summary-prompt-file", str(project["tmp"] / "nope.txt")], "@news\n")
    assert rc == 2
    assert "cannot read" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Logs
# --------------------------------------------------------------------------
def test_log_created_and_free_of_secrets(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, [_now_message(text="secret? no")])
    run_main(monkeypatch, project, fake_client, ["--last", "1h"], "@news\n")
    log = project["logs"] / "run.log"
    assert log.exists()
    text = log.read_text(encoding="utf-8")
    assert "10000000000" not in text   # stubbed phone
    assert "hash" not in text.lower()  # stubbed api hash marker
    assert "api_hash" not in text.lower()


# --------------------------------------------------------------------------
# .gitignore
# --------------------------------------------------------------------------
def test_gitignore_covers_secrets_and_artifacts():
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", "*.session", "output/", ".cache/", "state.json"):
        assert pattern in text, f"{pattern} missing from .gitignore"


def test_no_secret_or_artifact_files_are_git_tracked():
    if not (ROOT / ".git").exists():
        pytest.skip("not a git working tree")
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True).stdout
    tracked = out.splitlines()
    bad = [f for f in tracked if f == ".env" or f.endswith(".session")
           or f.startswith("output/") or f.startswith(".cache/") or f == "state.json"]
    assert bad == [], f"secret/artifact files tracked: {bad}"
