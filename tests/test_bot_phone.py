"""Phone + code login flow."""
import asyncio
import time

import pytest
from telethon.errors import FloodWaitError, PhoneNumberInvalidError

from fake_bot_client import FakeEvent, build_app

OWNER = 555


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def _start_phone_login(app, bot, phone="+919812345678"):
    asyncio.run(app.handle(FakeEvent(bot, "/login_phone", OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, phone, OWNER)))


def test_phone_success(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.sign_in_script = [("ok",)]
    asyncio.run(app.handle(FakeEvent(bot, "/login_phone", OWNER)))
    assert app.manager.state.awaiting == "phone"

    phone_event = FakeEvent(bot, "+919812345678", OWNER)
    asyncio.run(app.handle(phone_event))
    assert phone_event.deleted
    assert app.manager.state.awaiting == "code"
    assert any(c[0] == "send_code_request" for c in user.calls)

    code_event = FakeEvent(bot, "1-2-3-4-5", OWNER)
    asyncio.run(app.handle(code_event))
    assert code_event.deleted
    sign_in = [c for c in user.calls if c[0] == "sign_in"][-1]
    assert sign_in[1]["code"] == "12345"
    assert not app.manager.active


@pytest.mark.parametrize("typed", ["1-2-3-4-5", "1 2 3 4 5", "12345", "(1)2 3-4 5", "1.2.3.4.5"])
def test_code_separators_are_stripped(tmp_path, typed):
    app, bot, user, owners, session = build_app(tmp_path)
    user.sign_in_script = [("ok",)]
    _start_phone_login(app, bot)
    asyncio.run(app.handle(FakeEvent(bot, typed, OWNER)))
    sign_in = [c for c in user.calls if c[0] == "sign_in"][-1]
    assert sign_in[1]["code"] == "12345"


def test_invalid_code_keeps_flow_alive(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.sign_in_script = [("code_invalid",)]
    _start_phone_login(app, bot)
    asyncio.run(app.handle(FakeEvent(bot, "1-2-3-4-5", OWNER)))
    assert app.manager.active
    assert app.manager.state.code_attempts == 1
    assert any("गलत" in t for t in _texts(bot))


def test_three_invalid_codes_end_flow(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.sign_in_script = [("code_invalid",)] * 3
    _start_phone_login(app, bot)
    for _ in range(3):
        asyncio.run(app.handle(FakeEvent(bot, "11111", OWNER)))
    assert not app.manager.active


def test_expired_code_mentions_chat_block(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.sign_in_script = [("code_expired",)]
    _start_phone_login(app, bot)
    asyncio.run(app.handle(FakeEvent(bot, "1-2-3-4-5", OWNER)))
    assert not app.manager.active
    assert any("block" in t for t in _texts(bot))


def test_send_code_rate_limit(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    app._code_times = [time.time()] * 3          # already 3 in the last hour
    asyncio.run(app.handle(FakeEvent(bot, "/login_phone", OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, "+919812345678", OWNER)))
    assert not any(c[0] == "send_code_request" for c in user.calls)
    assert any("बहुत ज़्यादा" in t for t in _texts(bot))


def test_flood_wait_stops_cleanly(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.send_code_error = FloodWaitError(request=None, capture=42)
    asyncio.run(app.handle(FakeEvent(bot, "/login_phone", OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, "+919812345678", OWNER)))
    assert any("42" in t for t in _texts(bot))
    assert not app.manager.active


def test_invalid_phone_number(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.send_code_error = PhoneNumberInvalidError(request=None)
    asyncio.run(app.handle(FakeEvent(bot, "/login_phone", OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, "+919812345678", OWNER)))
    assert any("invalid" in t.lower() for t in _texts(bot))


def test_malformed_phone_is_rejected_before_network(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/login_phone", OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, "hello", OWNER)))
    assert not any(c[0] == "send_code_request" for c in user.calls)
    assert app.manager.state.awaiting == "phone"
