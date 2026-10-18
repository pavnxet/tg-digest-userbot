"""Phase 3 (top-up): reach 100% on time/links/splitter/formatter and lift others."""
import asyncio
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon.errors import ChannelBannedError, ChannelPrivateError, UsernameInvalidError
from telethon.tl import types as t

import config
import timeutil
from channels import ChannelSpec, JoinState, resolve_spec
from fake_client import FakeTelegramClient, make_channel, make_message
from formatter import build_link_base, clean, ist_str
from scraper import ScrapeOptions, count_channel, media_label
from splitter import split_digest
from timeutil import Window, get_tz, resolve_window

ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc
BASE = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------
# timeutil 100%
# --------------------------------------------------------------------------
def test_get_tz_fallback_for_ist_when_zoneinfo_missing(monkeypatch):
    def _boom(*args, **kwargs):
        raise timeutil.ZoneInfoNotFoundError("no tzdata")

    monkeypatch.setattr(timeutil, "ZoneInfo", _boom)
    tz = get_tz("Asia/Kolkata")
    assert tz.utcoffset(None) == timedelta(hours=5, minutes=30)


def test_get_tz_reraises_for_unknown_zone(monkeypatch):
    def _boom(*args, **kwargs):
        raise timeutil.ZoneInfoNotFoundError("no tzdata")

    monkeypatch.setattr(timeutil, "ZoneInfo", _boom)
    with pytest.raises(timeutil.ZoneInfoNotFoundError):
        get_tz("Europe/Nowhere")


def test_until_without_since_errors():
    with pytest.raises(ValueError):
        resolve_window(until="2026-10-18 06:00", tz=get_tz())


def test_window_contains():
    window = Window(BASE, BASE + timedelta(hours=1))
    assert window.contains(BASE)
    assert not window.contains(BASE - timedelta(seconds=1))


# --------------------------------------------------------------------------
# formatter 100% (ist_str naive branch, clean list branch, links)
# --------------------------------------------------------------------------
def test_ist_str_naive_is_treated_as_utc():
    assert ist_str(datetime(2026, 10, 18, 3, 44)) == "2026-10-18 09:14"


def test_clean_drops_empty_items_inside_lists():
    assert clean(["a", "", None, False, [], {}]) == ["a"]


def test_build_link_base_matrix():
    assert build_link_base(123, "user") == "https://t.me/user/"
    assert build_link_base(-1001234567890, None) == "https://t.me/c/1234567890/"
    assert build_link_base(-123456, None) == "https://t.me/c/123456/"
    assert build_link_base(123456, None) == "https://t.me/c/123456/"


# --------------------------------------------------------------------------
# splitter 100%
# --------------------------------------------------------------------------
def test_split_digest_rejects_non_positive_max_chars():
    with pytest.raises(ValueError):
        split_digest("i", {"part": "1/1"}, [], 0)


# --------------------------------------------------------------------------
# scraper: media labels + count_channel
# --------------------------------------------------------------------------
def _doc(attributes):
    return t.MessageMediaDocument(
        document=t.Document(id=1, access_hash=1, file_reference=b"", date=BASE,
                            mime_type="x", size=1, dc_id=1, attributes=attributes)
    )


@pytest.mark.parametrize(
    "attrs,expected",
    [
        ([t.DocumentAttributeVideo(duration=1, w=1, h=1)], "video"),
        ([t.DocumentAttributeVideo(duration=1, w=1, h=1, round_message=True)], "video"),
        ([t.DocumentAttributeAudio(duration=1, voice=True)], "voice"),
        ([t.DocumentAttributeAudio(duration=1, title="song")], "audio"),
        ([t.DocumentAttributeSticker(alt="x", stickerset=t.InputStickerSetEmpty())], "sticker"),
        ([t.DocumentAttributeAnimated()], "gif"),
        ([], "document"),
        ([t.DocumentAttributeFilename(file_name="a.pdf")], "document:a.pdf"),
    ],
)
def test_media_label_variants(attrs, expected):
    assert media_label(make_message(1, BASE, media=_doc(attrs))) == expected


def test_count_channel_counts_window():
    fake = FakeTelegramClient()
    ch = make_channel(1, "C", username="c")
    fake.add_channel(ch, [make_message(i, BASE - i * timedelta(minutes=1), text="x") for i in range(5)])
    opts = ScrapeOptions(tz=get_tz())
    window = Window(BASE - timedelta(minutes=3), BASE + timedelta(minutes=1))
    n = asyncio.run(count_channel(fake, SimpleNamespace(entity=ch), window, opts))
    assert n == 4


# --------------------------------------------------------------------------
# client: build + session detection (no network)
# --------------------------------------------------------------------------
def test_build_client_and_session_detection(tmp_path):
    import client as client_mod
    from config import Credentials

    # session_file_exists is pure filesystem logic.
    assert client_mod.session_file_exists(str(tmp_path / "fresh")) is False
    (tmp_path / "s.session").write_text("x", encoding="utf-8")
    assert client_mod.session_file_exists(str(tmp_path / "s")) is True

    c = client_mod.build_client(Credentials(api_id=123, api_hash="h", phone="+1"), str(tmp_path / "cli"))
    assert c.api_id == 123


# --------------------------------------------------------------------------
# channels: resolve error branches
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "error,reason",
    [
        (UsernameInvalidError(request=None), "invalid_username"),
        (ChannelPrivateError(request=None), "private"),
        (ChannelBannedError(request=None), "banned"),
        (ValueError("nope"), "not_found"),
        (RuntimeError("boom"), "error:RuntimeError"),
    ],
)
def test_resolve_spec_error_branches(error, reason):
    fake = FakeTelegramClient()
    fake.get_entity_errors["x"] = error
    spec = ChannelSpec(raw="@x", kind="username", value="x")
    resolved = asyncio.run(resolve_spec(fake, spec, JoinState()))
    assert resolved.ok is False and resolved.reason == reason


# --------------------------------------------------------------------------
# config: ensure_directories
# --------------------------------------------------------------------------
def test_ensure_directories_creates(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "o")
    monkeypatch.setattr(config, "CACHE_DIR", tmp_path / "c")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "l")
    config.ensure_directories()
    assert (tmp_path / "o").is_dir() and (tmp_path / "c").is_dir() and (tmp_path / "l").is_dir()


# --------------------------------------------------------------------------
# main(): CLI entry point via subprocess (help + friendly error path)
# --------------------------------------------------------------------------
def test_cli_help_exit_zero():
    result = subprocess.run([sys.executable, "main.py", "--help"], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0 and "usage" in result.stdout.lower()


def test_cli_missing_credentials_is_friendly(tmp_path):
    channels = tmp_path / "channels.txt"
    channels.write_text("@news\n", encoding="utf-8")
    env = dict(__import__("os").environ)
    env.pop("API_ID", None); env.pop("API_HASH", None); env.pop("PHONE", None)
    result = subprocess.run(
        [sys.executable, "main.py", "--today", "--channels-file", str(channels)],
        cwd=ROOT, capture_output=True, text=True, env=env,
    )
    assert result.returncode == 2
    assert "Error:" in result.stderr and "Traceback" not in result.stderr
