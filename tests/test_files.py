"""Section F: file listing, retention safety, /clean, /files and /find."""
import asyncio
import json
import os
import time
from datetime import datetime, timedelta

from fake_bot_client import FakeCallbackEvent, FakeEvent, build_app
from files_store import clean_older_than, find, list_outputs
from timeutil import get_tz

OWNER = 555
TZ = get_tz()


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def _digest(path, channels, messages_total=1):
    path.write_text(json.dumps({
        "meta": {"messages_total": messages_total},
        "channels": channels,
    }), encoding="utf-8")


def _old(path, days):
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def _app(tmp_path):
    script = tmp_path / "fake_main.py"
    script.write_text("pass\n", encoding="utf-8")
    return build_app(tmp_path, channels_text="@a\n", run_script=script)


# --------------------------------------------------------------------------
# files_store
# --------------------------------------------------------------------------
def test_list_outputs_counts_messages(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    _digest(out / "digest_a.json", [{"name": "A", "link_base": "https://t.me/a/", "messages": []}], messages_total=7)
    infos = list_outputs(out)
    assert infos[0].name == "digest_a.json" and infos[0].messages == 7


def test_retention_deletes_old_keeps_new(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    old = out / "digest_old.json"
    new = out / "digest_new.json"
    _digest(old, [], 1)
    _digest(new, [], 1)
    _old(old, 10)
    deleted = clean_older_than(out, days=7)
    assert deleted == ["digest_old.json"]
    assert new.exists() and not old.exists()


def test_retention_skips_escaping_symlink(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    outside = tmp_path / "outside.json"
    _digest(outside, [], 1)
    _old(outside, 30)
    link = out / "digest_link.json"
    try:
        link.symlink_to(outside)
    except OSError:
        return  # filesystem without symlinks
    assert list_outputs(out) == []            # the escaping symlink is ignored
    clean_older_than(out, days=7)
    assert outside.exists()                   # the target was never deleted


def test_retention_never_touches_cache(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    cache = tmp_path / ".cache"
    cache.mkdir()
    (cache / "run_history.json").write_text("[]", encoding="utf-8")
    (cache / "run.lock").write_text("{}", encoding="utf-8")
    clean_older_than(out, days=1)
    assert (cache / "run_history.json").exists() and (cache / "run.lock").exists()


def test_find_hindi_nfc_and_casefold(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    # decomposed Devanagari (NFD) must still match an NFC query
    import unicodedata

    word = unicodedata.normalize("NFD", "समाचार")
    _digest(out / "digest_x.json", [{
        "name": "News", "link_base": "https://t.me/n/",
        "messages": [{"id": 5, "t": datetime.now(TZ).isoformat(), "text": f"आज का {word} महत्वपूर्ण"}],
    }])
    assert find("समाचार", out)
    matches = find("समाचार", out)
    assert matches[0]["link"] == "https://t.me/n/5"
    assert len(matches[0]["snippet"]) <= 200


def test_find_respects_window(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    old_time = (datetime.now(TZ) - timedelta(hours=30)).isoformat()
    _digest(out / "digest_x.json", [{
        "name": "News", "link_base": "",
        "messages": [{"id": 1, "t": old_time, "text": "keyword here"}],
    }])
    assert find("keyword", out, window_hours=6) == []
    assert find("keyword", out, window_hours=48)


def test_find_limit(tmp_path):
    out = tmp_path / "output"
    out.mkdir()
    messages = [{"id": i, "t": datetime.now(TZ).isoformat(), "text": "hit"} for i in range(50)]
    _digest(out / "digest_x.json", [{"name": "N", "link_base": "", "messages": messages}])
    assert len(find("hit", out, limit=10)) == 10


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def test_files_lists_and_resends(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    out = tmp_path / "output"
    _digest(out / "digest_a.json", [{"name": "A", "link_base": "", "messages": []}], messages_total=3)
    asyncio.run(app.handle(FakeEvent(bot, "/files", OWNER)))
    assert "digest_a.json" in _texts(bot)[-1]
    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, b"file:1", OWNER)))
    assert bot.files


def test_clean_confirm_deletes(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    out = tmp_path / "output"
    _digest(out / "digest_old.json", [], 1)
    _old(out / "digest_old.json", 30)
    asyncio.run(app.handle(FakeEvent(bot, "/clean", OWNER)))
    assert "हटाएँ" in _texts(bot)[-1]
    token = list(app._pending)[0]
    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, f"confirm:{token}".encode(), OWNER)))
    assert not (out / "digest_old.json").exists()


def test_find_command(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    out = tmp_path / "output"
    _digest(out / "digest_x.json", [{
        "name": "News", "link_base": "https://t.me/n/",
        "messages": [{"id": 2, "t": datetime.now(TZ).isoformat(), "text": "महत्वपूर्ण समाचार"}],
    }])
    asyncio.run(app.handle(FakeEvent(bot, "/find समाचार", OWNER)))
    assert "समाचार" in _texts(bot)[-1]


def test_file_commands_owner_guarded(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    for command in ["/files", "/clean", "/find x"]:
        event = FakeEvent(bot, command, 999)
        asyncio.run(app.handle(event))
        assert event.replies == []
    assert bot.sent == []
