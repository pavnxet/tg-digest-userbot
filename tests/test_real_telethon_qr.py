"""Real Telethon objects for the QR and error paths.

Last time a fake and the real library disagreed and hid a bug, so the QR token
encoding and the error classes are checked against the installed Telethon.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.tl.custom.qrlogin import QRLogin

import bot_handlers


class _StubClient:
    """Minimal client so a real QRLogin can be constructed offline."""

    api_id = 1
    api_hash = "hash"

    def __init__(self, token: bytes) -> None:
        self._token = token

    async def __call__(self, request):  # ExportLoginTokenRequest
        return SimpleNamespace(token=self._token, expires=datetime.now(timezone.utc) + timedelta(seconds=30))


def test_real_qr_url_uses_urlsafe_base64():
    # b"\xfb\xff" -> standard base64 "+/8=" (contains + and /), URL-safe "-_8=".
    token = b"\xfb\xff"
    qr = QRLogin(_StubClient(token), [])
    asyncio.run(qr.recreate())
    expected = "tg://login?token=" + base64.urlsafe_b64encode(token).decode().rstrip("=")
    assert qr.url == expected
    payload = qr.url.split("token=", 1)[1]
    assert "+" not in payload and "/" not in payload  # the old upstream bug would put these here
    # And confirm the installed library really uses urlsafe_b64encode.
    import inspect

    import telethon.tl.custom.qrlogin as qrlogin

    source = inspect.getsource(qrlogin)
    assert "urlsafe_b64encode" in source
    assert "base64.b64encode" not in source


def test_real_error_classes_are_the_ones_we_catch():
    for name in (
        "SessionPasswordNeededError",
        "PhoneCodeInvalidError",
        "PhoneCodeExpiredError",
        "PhoneNumberInvalidError",
        "PhoneNumberBannedError",
        "PasswordHashInvalidError",
        "FloodWaitError",
    ):
        assert getattr(bot_handlers, name) is not None


@pytest.mark.parametrize(
    "error",
    [
        SessionPasswordNeededError(request=None),
        PhoneCodeInvalidError(request=None),
        PhoneCodeExpiredError(request=None),
        PhoneNumberInvalidError(request=None),
        PhoneNumberBannedError(request=None),
        PasswordHashInvalidError(request=None),
        FloodWaitError(request=None, capture=7),
    ],
)
def test_real_errors_are_exceptions(error):
    assert isinstance(error, Exception)


def test_qr_login_object_shape():
    qr = QRLogin(_StubClient(b"\x01\x02\x03"), [])
    asyncio.run(qr.recreate())
    assert isinstance(qr.url, str) and qr.url.startswith("tg://login?token=")
    assert isinstance(qr.token, bytes)
    assert hasattr(qr, "recreate") and hasattr(qr, "wait")
