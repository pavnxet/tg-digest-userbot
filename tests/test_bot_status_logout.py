"""Status and logout."""
import asyncio

from fake_bot_client import FakeCallbackEvent, FakeEvent, build_app

OWNER = 555
PHONE = "+919812345678"


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def test_status_when_not_logged_in(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.authorized = False
    asyncio.run(app.handle(FakeEvent(bot, "/status", OWNER)))
    assert any("Logged in नहीं" in t for t in _texts(bot))


def test_status_shows_only_last_two_digits(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.authorized = True
    user.me_obj.phone = PHONE
    user.me_obj.id = 123456789
    asyncio.run(app.handle(FakeEvent(bot, "/status", OWNER)))
    joined = "\n".join(_texts(bot))
    assert "…78" in joined
    assert PHONE not in joined          # never the full number
    assert "123456789" not in joined    # never the user id


def test_logout_confirmation_then_yes(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.authorized = True
    (tmp_path / "tg_digest.session").write_text("x", encoding="utf-8")

    asyncio.run(app.handle(FakeEvent(bot, "/logout", OWNER)))
    assert any("पक्का logout" in t for t in _texts(bot))

    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, b"logout_yes", OWNER)))
    assert any(c[0] == "log_out" for c in user.calls)
    assert not (tmp_path / "tg_digest.session").exists()
    assert any("Logged out" in t for t in _texts(bot))


def test_logout_cancel_does_not_log_out(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.authorized = True
    asyncio.run(app.handle(FakeEvent(bot, "/logout", OWNER)))
    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, b"logout_no", OWNER)))
    assert not any(c[0] == "log_out" for c in user.calls)
    assert any("रद्द" in t for t in _texts(bot))


def test_logout_partial_failure_is_reported(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.authorized = True
    user.log_out_result = False
    asyncio.run(app.handle(FakeEvent(bot, "/logout", OWNER)))
    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, b"logout_yes", OWNER)))
    assert any("पूरा नहीं हुआ" in t for t in _texts(bot))


def test_logout_when_already_logged_out(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.authorized = False
    asyncio.run(app.handle(FakeEvent(bot, "/logout", OWNER)))
    assert any("पहले से logged out" in t for t in _texts(bot))


def test_non_owner_callback_is_ignored(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.authorized = True
    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, b"logout_yes", 999)))
    assert not any(c[0] == "log_out" for c in user.calls)
    assert bot.sent == []
