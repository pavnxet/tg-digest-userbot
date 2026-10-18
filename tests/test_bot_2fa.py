"""2FA password step."""
import asyncio

from fake_bot_client import FakeEvent, build_app

OWNER = 555
PASSWORD = "S3cretPW-CANARY"


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def _to_2fa(app, bot, user):
    user.sign_in_script = [("password_needed",)] + user.sign_in_script
    asyncio.run(app.handle(FakeEvent(bot, "/login_phone", OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, "+919812345678", OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, "1-2-3-4-5", OWNER)))


def test_2fa_success(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.sign_in_script = [("password_needed",), ("ok",)]
    _to_2fa(app, bot, user)
    assert app.manager.state.awaiting == "password"

    password_event = FakeEvent(bot, PASSWORD, OWNER)
    asyncio.run(app.handle(password_event))
    assert password_event.deleted
    sign_in = [c for c in user.calls if c[0] == "sign_in"][-1]
    assert sign_in[1]["password"] == PASSWORD
    assert not app.manager.active


def test_wrong_password_three_times_ends_flow(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.sign_in_script = [("password_needed",)] + [("password_invalid",)] * 3
    _to_2fa(app, bot, user)
    for _ in range(3):
        asyncio.run(app.handle(FakeEvent(bot, "wrong", OWNER)))
    assert not app.manager.active


def test_password_is_not_retained_in_state(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.sign_in_script = [("password_needed",), ("ok",)]
    _to_2fa(app, bot, user)
    asyncio.run(app.handle(FakeEvent(bot, PASSWORD, OWNER)))

    state = app.manager.state
    assert not hasattr(state, "password")
    assert PASSWORD not in repr(state)
    assert PASSWORD not in repr(app.manager)
    assert PASSWORD not in str(vars(state))
    assert not any(PASSWORD in t for t in _texts(bot))
