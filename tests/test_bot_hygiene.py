"""Message hygiene and the no-secrets rule."""
import asyncio
import logging

from fake_bot_client import FakeEvent, build_app

OWNER = 555
CODE_TYPED = "9-8-7-6-5"
CODE_DIGITS = "98765"
PHONE = "+919800011122"
PASSWORD = "PW-CANARY-42"


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def _run_full_phone_flow(app, bot, user):
    user.sign_in_script = [("password_needed",), ("ok",)]
    asyncio.run(app.handle(FakeEvent(bot, "/login_phone", OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, PHONE, OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, CODE_TYPED, OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, PASSWORD, OWNER)))


def test_sensitive_messages_are_deleted(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.sign_in_script = [("password_needed",), ("ok",)]
    asyncio.run(app.handle(FakeEvent(bot, "/login_phone", OWNER)))
    for value in (PHONE, CODE_TYPED, PASSWORD):
        event = FakeEvent(bot, value, OWNER)
        asyncio.run(app.handle(event))
        assert event.deleted, f"{value!r} was not deleted"


def test_claim_message_is_deleted(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, needs_pairing=True)
    code = owners.start_pairing()
    event = FakeEvent(bot, f"/claim {code}", 777)
    asyncio.run(app.handle(event))
    assert event.deleted


def test_delete_failure_triggers_manual_warning(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    bot.delete_should_fail = True
    user.sign_in_script = [("ok",)]
    asyncio.run(app.handle(FakeEvent(bot, "/login_phone", OWNER)))
    event = FakeEvent(bot, PHONE, OWNER)
    asyncio.run(app.handle(event))
    assert any("delete" in t and "मैन्युअल" in t for t in _texts(bot))


def test_no_secrets_in_logs_or_outgoing_messages(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    app, bot, user, owners, session = build_app(tmp_path)
    _run_full_phone_flow(app, bot, user)

    for canary in (CODE_DIGITS, PASSWORD, PHONE):
        assert canary not in caplog.text, f"{canary!r} leaked into the logs"
        assert canary not in "\n".join(_texts(bot)), f"{canary!r} leaked into a bot message"


def test_qr_token_not_logged(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    app, bot, user, owners, session = build_app(tmp_path)
    user.qr_script = [("ok",)]

    async def scenario():
        await app.handle(FakeEvent(bot, "/login", OWNER))
        await app.manager.state.qr_task

    asyncio.run(scenario())
    assert "CANARYTOKEN" not in caplog.text


def test_no_secrets_in_the_bot_log_file(tmp_path):
    from logging_setup import setup_logging

    root = logging.getLogger()
    saved = list(root.handlers)
    try:
        setup_logging(tmp_path / "bot.log", verbose=False)
        app, bot, user, owners, session = build_app(tmp_path)
        _run_full_phone_flow(app, bot, user)
        for handler in root.handlers:
            handler.flush()
        content = (tmp_path / "bot.log").read_text(encoding="utf-8")
        for canary in (CODE_DIGITS, PASSWORD, PHONE):
            assert canary not in content
    finally:
        root.handlers[:] = saved


def test_timeout_clears_state(tmp_path):
    import time

    app, bot, user, owners, session = build_app(tmp_path)
    user.sign_in_script = [("ok",)]
    asyncio.run(app.handle(FakeEvent(bot, "/login_phone", OWNER)))
    app.manager.state.phone = PHONE
    app.manager.state.last_activity = time.time() - 10_000  # way past the timeout

    asyncio.run(app.handle(FakeEvent(bot, "/help", OWNER)))
    assert not app.manager.active
    assert app.manager.state.phone is None
