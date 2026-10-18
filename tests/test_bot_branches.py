"""Error-branch coverage for the bot handlers (they must never crash)."""
import asyncio
import sqlite3

from fake_bot_client import FakeEvent, build_app

OWNER = 555


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def test_unexpected_connect_error_on_login_is_friendly(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.connect_error = RuntimeError("boom")
    asyncio.run(app.handle(FakeEvent(bot, "/login", OWNER)))
    assert any("connect नहीं हो सका" in t for t in _texts(bot))


def test_unexpected_connect_error_on_status_is_friendly(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.connect_error = RuntimeError("boom")
    asyncio.run(app.handle(FakeEvent(bot, "/status", OWNER)))
    assert any("connect नहीं हो सका" in t for t in _texts(bot))


def test_locked_session_on_logout(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.connect_error = sqlite3.OperationalError("database is locked")
    asyncio.run(app.handle(FakeEvent(bot, "/logout", OWNER)))
    assert any("locked" in t for t in _texts(bot))


def test_qr_send_failure_is_survived(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.qr_script = [("ok",)]
    bot.send_should_fail = True

    async def scenario():
        await app.handle(FakeEvent(bot, "/login", OWNER))
        await app.manager.state.qr_task

    asyncio.run(scenario())
    assert not app.manager.active


def test_qr_delete_failure_is_survived(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.qr_script = [("timeout",), ("ok",)]
    bot.delete_should_fail = True

    async def scenario():
        await app.handle(FakeEvent(bot, "/login", OWNER))
        await app.manager.state.qr_task

    asyncio.run(scenario())
    assert not app.manager.active


def test_qr_recreate_failure_ends_flow(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.qr_script = [("timeout",), ("ok",)]

    async def scenario():
        await app.handle(FakeEvent(bot, "/login", OWNER))
        user.qr.recreate_should_fail = True
        await app.manager.state.qr_task

    asyncio.run(scenario())
    assert any("refresh नहीं" in t for t in _texts(bot))
    assert not app.manager.active


def test_bot_reply_failure_is_swallowed(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    bot.send_should_fail = True
    # /help would reply, but sending fails; the handler must not raise.
    asyncio.run(app.handle(FakeEvent(bot, "/help", OWNER)))
    assert bot.sent == []
