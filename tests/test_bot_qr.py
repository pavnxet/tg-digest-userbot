"""QR login flow."""
import asyncio

from fake_bot_client import FakeEvent, build_app

OWNER = 555
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _sent_texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def test_qr_success_sends_image_and_url_then_clears_state(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.qr_script = [("ok",)]

    async def scenario():
        await app.handle(FakeEvent(bot, "/login", OWNER))
        task = app.manager.state.qr_task
        assert task is not None
        await task

    asyncio.run(scenario())

    assert bot.files and bot.files[0][1][:8] == PNG_MAGIC          # QR image, in memory
    assert "tg://login?token=" in "\n".join(_sent_texts(bot))       # URL sent as text
    assert any("Login हो गया" in t for t in _sent_texts(bot))
    assert not app.manager.active
    qr_id = bot.files[0][3].id
    assert any(qr_id in ids for _chat, ids in bot.deleted)          # last QR deleted


def test_qr_timeout_refreshes_then_stops(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.qr_script = [("timeout",)] * 4   # initial + 3 refreshes all expire

    async def scenario():
        await app.handle(FakeEvent(bot, "/login", OWNER))
        await app.manager.state.qr_task

    asyncio.run(scenario())

    assert user.qr.recreates == 3           # capped at 3 refreshes
    assert len(bot.files) == 4              # initial QR + 3 refreshed QRs
    assert len(bot.deleted) >= 3            # old QR deleted on each refresh
    assert any("expire" in t for t in _sent_texts(bot))
    assert not app.manager.active


def test_qr_old_message_deleted_on_refresh(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.qr_script = [("timeout",), ("ok",)]

    async def scenario():
        await app.handle(FakeEvent(bot, "/login", OWNER))
        await app.manager.state.qr_task

    asyncio.run(scenario())
    first_qr_id = bot.files[0][3].id
    assert any(first_qr_id in ids for _chat, ids in bot.deleted)


def test_qr_then_2fa_required(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.qr_script = [("password",)]

    async def scenario():
        await app.handle(FakeEvent(bot, "/login", OWNER))
        await app.manager.state.qr_task

    asyncio.run(scenario())
    assert app.manager.active
    assert app.manager.state.flow == "2fa"
    assert app.manager.state.awaiting == "password"


def test_cancel_during_qr_wait(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.qr_script = [("block",)]  # wait blocks until cancelled

    async def scenario():
        await app.handle(FakeEvent(bot, "/login", OWNER))
        task = app.manager.state.qr_task
        await asyncio.sleep(0)                      # let the task reach wait()
        await app.handle(FakeEvent(bot, "/cancel", OWNER))
        assert not app.manager.active
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert any("रद्द" in t for t in _sent_texts(bot))


def test_login_while_authorized_says_already(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.authorized = True
    asyncio.run(app.handle(FakeEvent(bot, "/login", OWNER)))
    assert any("पहले से logged in" in t for t in _sent_texts(bot))
    assert not app.manager.active


def test_second_login_while_active_is_refused(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.qr_script = [("block",)]

    async def scenario():
        await app.handle(FakeEvent(bot, "/login", OWNER))
        task = app.manager.state.qr_task
        await asyncio.sleep(0)
        await app.handle(FakeEvent(bot, "/login", OWNER))
        task.cancel()  # release the still-blocked first task
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert any("पहले से चल रहा" in t for t in _sent_texts(bot))
