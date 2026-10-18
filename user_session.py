"""The ONLY place the user client is touched.

Read-only by contract. Every method here calls a strict allowlist of client
methods (login, status, logout). Nothing sends, edits, deletes, forwards or marks
read. The single non-read call is ``log_out()``, used only by /logout.

The safety scan (tests/test_phase2_readonly.py) enforces this allowlist by
inspecting the receiver of every call in this file.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Callable

from telethon.tl.functions.messages import CheckChatInviteRequest

from client import build_client
from config import Credentials

_log = logging.getLogger("tgdigest.bot.user")

# The exact set of client methods this wrapper is allowed to call. The scan
# asserts every call on the user client is in this set.
USER_CLIENT_METHODS = frozenset({
    "connect",
    "disconnect",
    "is_user_authorized",
    "get_me",
    "get_entity",
    "qr_login",
    "send_code_request",
    "sign_in",
    "log_out",
})

# Request classes the wrapper may pass to the user client (all read-only).
USER_CLIENT_REQUESTS = frozenset({"CheckChatInviteRequest"})


class SessionLockedError(RuntimeError):
    """The session file is in use by another process (SQLite 'database is locked')."""


class UserSession:
    """Connect the user client only for login / status / logout, then disconnect."""

    def __init__(
        self,
        credentials: Credentials,
        session_basename: str,
        client_factory: Callable[..., Any] = build_client,
    ) -> None:
        self._credentials = credentials
        self._session_basename = str(session_basename)
        self._factory = client_factory
        self._client: Any = None

    @property
    def session_path(self) -> Path:
        return Path(f"{self._session_basename}.session")

    async def connect(self) -> None:
        if self._client is None:
            self._client = self._factory(self._credentials, self._session_basename)
        try:
            await self._client.connect()
        except sqlite3.OperationalError as exc:
            self._client = None
            if "locked" in str(exc).lower():
                raise SessionLockedError("session database is locked") from None
            raise
        self._harden_session_file()

    async def disconnect(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            finally:
                self._client = None

    def _harden_session_file(self) -> None:
        try:
            if self.session_path.exists():
                self.session_path.chmod(0o600)
        except OSError:
            _log.warning("could not chmod 600 the user session file (non-POSIX filesystem?)")

    # -- allowlisted client calls -----------------------------------------
    async def is_authorized(self) -> bool:
        return bool(await self._client.is_user_authorized())

    async def me(self) -> Any:
        return await self._client.get_me()

    async def get_entity(self, key: Any) -> Any:
        return await self._client.get_entity(key)

    async def check_invite(self, invite_hash: str) -> Any:
        """Read-only: check an invite link without ever importing/joining it."""
        return await self._client(CheckChatInviteRequest(invite_hash))

    async def qr_login(self) -> Any:
        return await self._client.qr_login()

    async def send_code_request(self, phone: str) -> Any:
        return await self._client.send_code_request(phone)

    async def sign_in(self, **kwargs: Any) -> Any:
        return await self._client.sign_in(**kwargs)

    async def log_out(self) -> bool:
        return bool(await self._client.log_out())

    def delete_session_file(self) -> bool:
        try:
            self.session_path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False
