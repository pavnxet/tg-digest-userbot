"""Section G: /health and /panic (double confirmation, .cache exclusions)."""
import asyncio
import json

from fake_bot_client import FakeCallbackEvent, FakeEvent, build_app

OWNER = 555


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def _app(tmp_path):
    script = tmp_path / "fake_main.py"
    script.write_text("pass\n", encoding="utf-8")
    return build_app(tmp_path, channels_text="@a\n", run_script=script)


def _press(app, bot, token):
    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, f"confirm:{token}".encode(), OWNER)))


def test_health_reports_without_secrets(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    user.authorized = True
    user.me_obj.phone = "+919812345678"
    (tmp_path / "tg_digest.session").write_text("x", encoding="utf-8")
    asyncio.run(app.handle(FakeEvent(bot, "/health", OWNER)))
    text = _texts(bot)[-1]
    assert "logged in: yes" in text
    assert "telethon" in text and "bot uptime" in text
    assert "+919812345678" not in text          # no full phone number
    assert "123456789" not in text


def test_panic_needs_two_confirmations(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/panic", OWNER)))
    assert "PANIC" in _texts(bot)[-1]
    _press(app, bot, list(app._pending)[0])
    assert "आख़िरी बार" in _texts(bot)[-1]      # second confirmation requested


def test_panic_second_button_has_a_delay(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    app._health_panel.panic_delay = 5
    asyncio.run(app.handle(FakeEvent(bot, "/panic", OWNER)))
    _press(app, bot, list(app._pending)[0])
    _press(app, bot, list(app._pending)[0])     # too soon
    assert "रुकें" in _texts(bot)[-1]


def test_panic_does_everything_and_keeps_the_right_files(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    user.authorized = True
    app._health_panel.panic_delay = 0
    # fixtures
    (tmp_path / "tg_digest.session").write_text("x", encoding="utf-8")
    out = tmp_path / "output"
    (out / "digest_a.json").write_text(json.dumps({"meta": {}}), encoding="utf-8")
    cache = tmp_path / "cache"
    (cache / "run_history.json").write_text("[]", encoding="utf-8")
    (cache / "run.lock").write_text("{}", encoding="utf-8")
    (cache / "checkpoint.jsonl").write_text("x\n", encoding="utf-8")
    app._schedule_panel.store.add("09:00", "today")
    app._settings_panel.settings.set("format", "json")   # persist settings.json

    asyncio.run(app.handle(FakeEvent(bot, "/panic", OWNER)))
    _press(app, bot, list(app._pending)[0])
    _press(app, bot, list(app._pending)[0])

    assert app._schedule_panel.store.all()[0].paused is True
    assert not (tmp_path / "tg_digest.session").exists()      # session deleted
    assert not (out / "digest_a.json").exists()               # outputs deleted
    assert (cache / "run_history.json").exists()              # limits preserved
    assert (cache / "run.lock").exists()
    assert not (cache / "checkpoint.jsonl").exists()          # other cache removed
    assert (tmp_path / "channels.txt").exists()               # kept
    assert (tmp_path / "settings.json").exists()              # kept
    assert "Panic done" in _texts(bot)[-1]


def test_health_and_panic_are_owner_guarded(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    for command in ["/health", "/panic"]:
        event = FakeEvent(bot, command, 999)
        asyncio.run(app.handle(event))
        assert event.replies == []
    assert bot.sent == []
