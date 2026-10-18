"""In-memory login state machine: one login at a time, 5-minute inactivity timeout.

No password is ever stored here (it lives in a local variable for the length of a
single call), and the phone/hash are dropped as soon as the flow ends.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

LOGIN_TIMEOUT_SECONDS = 300
QR_MAX_REFRESHES = 3
MAX_CODE_ATTEMPTS = 3
MAX_PASSWORD_ATTEMPTS = 3


class LoginInProgress(Exception):
    """Raised when a second login is started while one is active."""


@dataclass
class LoginState:
    flow: Optional[str] = None       # "qr" | "phone" | "2fa"
    awaiting: Optional[str] = None   # "phone" | "code" | "password"
    phone: Optional[str] = None
    phone_code_hash: Optional[str] = None
    code_attempts: int = 0
    password_attempts: int = 0
    qr: Any = None
    qr_refreshes: int = 0
    qr_task: Any = None
    qr_message_id: Optional[int] = None
    last_activity: float = 0.0

    def clear(self) -> None:
        """Drop every field, including any reference that could hold a secret."""
        self.flow = None
        self.awaiting = None
        self.phone = None
        self.phone_code_hash = None
        self.code_attempts = 0
        self.password_attempts = 0
        self.qr = None
        self.qr_refreshes = 0
        self.qr_task = None
        self.qr_message_id = None
        self.last_activity = 0.0


class LoginManager:
    """Owns the single LoginState and the inactivity timeout."""

    def __init__(self, timeout: float = LOGIN_TIMEOUT_SECONDS) -> None:
        self.state = LoginState()
        self.active = False
        self.timeout = timeout

    def begin(self, flow: str) -> LoginState:
        if self.active:
            raise LoginInProgress("a login is already in progress")
        self.state = LoginState(flow=flow, last_activity=time.time())
        self.active = True
        return self.state

    def touch(self) -> None:
        self.state.last_activity = time.time()

    def expired(self) -> bool:
        return self.active and (time.time() - self.state.last_activity) > self.timeout

    def tick(self) -> bool:
        """Clear the state if it timed out. Returns True when it was cleared."""
        if self.expired():
            self.clear()
            return True
        return False

    def clear(self) -> None:
        """Reset everything and cancel the background QR wait (unless we are in it)."""
        task = self.state.qr_task
        if task is not None and not task.done() and task is not self._current_task():
            task.cancel()
        self.state.clear()
        self.active = False

    @staticmethod
    def _current_task() -> Optional[Any]:
        try:
            return asyncio.current_task()
        except RuntimeError:  # no running loop
            return None

    def __repr__(self) -> str:  # never leak secrets
        return (
            f"LoginManager(active={self.active}, flow={self.state.flow!r}, "
            f"awaiting={self.state.awaiting!r})"
        )
