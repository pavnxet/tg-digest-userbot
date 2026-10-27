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
    token = next(iter(app._file_panel._files))
    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, f"file:{token}".encode(), OWNER)))
    assert bot.files


def test_files_button_resends_the_file_it_was_made_for(tmp_path):
    # An old button must never send a newer file just because the index matches.
    app, bot, user, owners, session = _app(tmp_path)
    out = tmp_path / "output"
    _digest(out / "digest_a.json", [], 1)
    asyncio.run(app.handle(FakeEvent(bot, "/files", OWNER)))
    old_token = next(iter(app._file_panel._files))
    _digest(out / "digest_b.json", [], 1)
    asyncio.run(app.handle(FakeEvent(bot, "/files", OWNER)))      # a newer list
    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, f"file:{old_token}".encode(), OWNER)))
    assert bot.files[-1][2] == "digest_a.json"


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


# --------------------------------------------------------------------------
# /clean: preview details, /clean all, /clean <N>d
# --------------------------------------------------------------------------
def _confirm(app, bot):
    """Press confirm on the last confirmation prompt."""
    token = list(app._pending)[0]
    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, f"confirm:{token}".encode(), OWNER)))


def test_clean_preview_shows_count_size_and_names(tmp_path):
    app, bot, *_ = _app(tmp_path)
    out = tmp_path / "output"
    _digest(out / "digest_old.json", [], 1)
    _digest(out / "digest_new.json", [], 1)
    _old(out / "digest_old.json", 10)          # older than the 7-day default
    asyncio.run(app.handle(FakeEvent(bot, "/clean", OWNER)))
    text = _texts(bot)[-1]
    assert "1 file" in text
    assert "digest_old.json" in text
    assert "digest_new.json" not in text
    assert "हटाएँ?" in text


def test_clean_all_previews_every_file(tmp_path):
    app, bot, *_ = _app(tmp_path)
    out = tmp_path / "output"
    _digest(out / "digest_a.json", [], 1)
    _digest(out / "digest_b.json", [], 1)
    asyncio.run(app.handle(FakeEvent(bot, "/clean all", OWNER)))
    text = _texts(bot)[-1]
    assert "2 file" in text and "सभी" in text


def test_clean_all_deletes_everything_after_confirmation(tmp_path):
    app, bot, *_ = _app(tmp_path)
    out = tmp_path / "output"
    _digest(out / "digest_a.json", [], 1)
    _digest(out / "digest_b.json", [], 1)
    asyncio.run(app.handle(FakeEvent(bot, "/clean all", OWNER)))
    _confirm(app, bot)
    assert list(out.glob("digest_*")) == []
    assert "2 file(s) हटाईं" in _texts(bot)[-1]


def test_clean_with_an_explicit_day_count(tmp_path):
    app, bot, *_ = _app(tmp_path)
    out = tmp_path / "output"
    _digest(out / "digest_old.json", [], 1)
    _digest(out / "digest_new.json", [], 1)
    _old(out / "digest_old.json", 5)
    asyncio.run(app.handle(FakeEvent(bot, "/clean 3d", OWNER)))
    text = _texts(bot)[-1]
    assert "digest_old.json" in text and "digest_new.json" not in text


def test_clean_with_nothing_to_delete(tmp_path):
    app, bot, *_ = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/clean", OWNER)))
    assert "कोई file नहीं मिली" in _texts(bot)[-1]


def test_clean_rejects_a_bad_argument(tmp_path):
    app, bot, *_ = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/clean banana", OWNER)))
    assert "उपयोग" in _texts(bot)[-1]


def test_days_arg_helper():
    from bot_files import _days_arg

    assert _days_arg("3d") == 3
    assert _days_arg("3") == 3
    assert _days_arg("banana") is None


def test_files_shows_the_total(tmp_path):
    app, bot, *_ = _app(tmp_path)
    out = tmp_path / "output"
    _digest(out / "digest_a.json", [], 1)
    asyncio.run(app.handle(FakeEvent(bot, "/files", OWNER)))
    assert "1 file(s)" in _texts(bot)[-1]
