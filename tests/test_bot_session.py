"""Session file handling: permissions, locked session, connect/disconnect scope."""
import asyncio
import os
import sqlite3
import stat

import pytest

from fake_bot_client import FakeEvent, build_app

OWNER = 555


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def test_session_file_is_chmod_600(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    path = tmp_path / "tg_digest.session"
    path.write_text("x", encoding="utf-8")
    asyncio.run(session.connect())
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_locked_session_on_status_is_friendly(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.connect_error = sqlite3.OperationalError("database is locked")
    asyncio.run(app.handle(FakeEvent(bot, "/status", OWNER)))
    assert any("locked" in t for t in _texts(bot))


def test_locked_session_on_login_is_friendly(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.connect_error = sqlite3.OperationalError("database is locked")
    asyncio.run(app.handle(FakeEvent(bot, "/login", OWNER)))
    assert any("locked" in t for t in _texts(bot))


def test_non_locked_sqlite_error_is_not_swallowed(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    user.connect_error = sqlite3.OperationalError("no such table: sessions")
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(session.connect())


def test_user_client_is_disconnected_after_status(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/status", OWNER)))
    assert user.connected is False
