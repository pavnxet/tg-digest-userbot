"""Section E: /settings and /set."""
import asyncio

from fake_bot_client import FakeEvent, build_app

OWNER = 555


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def _app(tmp_path):
    script = tmp_path / "fake_main.py"
    script.write_text("pass\n", encoding="utf-8")
    return build_app(tmp_path, run_script=script)


def test_settings_lists_all_keys(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/settings", OWNER)))
    text = _texts(bot)[-1]
    for key in ("format", "dedupe", "min_chars", "retention_days", "max_per_channel"):
        assert key in text


def test_set_valid_and_invalid(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/set min_chars 30", OWNER)))
    assert "✅" in _texts(bot)[-1]
    assert app._settings_panel.settings.get("min_chars") == 30
    asyncio.run(app.handle(FakeEvent(bot, "/set format xml", OWNER)))
    assert "❌" in _texts(bot)[-1]
    assert app._settings_panel.settings.get("format") == "json"


def test_settings_commands_are_owner_guarded(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    for command in ["/settings", "/set min_chars 5"]:
        event = FakeEvent(bot, command, 999)
        asyncio.run(app.handle(event))
        assert event.replies == []
    assert bot.sent == []


# --------------------------------------------------------------------------
# Phase 2: /settings must explain itself
# --------------------------------------------------------------------------
def test_settings_shows_what_each_key_does_and_its_range(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/settings", OWNER)))
    text = _texts(bot)[-1]
    assert "json · jsonl · txt" in text          # allowed values for format
    assert "0-500" in text                        # the range for min_chars
    assert "1-60" in text                         # the range for retention_days
    assert "/set" in text                         # how to change one


def test_set_unknown_key_lists_the_valid_keys(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/set nope 1", OWNER)))
    text = _texts(bot)[-1]
    assert "मान्य keys" in text
    assert "retention_days" in text


def test_set_without_a_value_lists_the_valid_keys(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/set dedupe", OWNER)))
    assert "मान्य keys" in _texts(bot)[-1]
