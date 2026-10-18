"""Owner lock and one-time pairing.

The owner is identified by numeric Telegram user id, never by username. The id
lives in a git-ignored file with chmod 600. When OWNER_ID is absent the bot uses
one-time pairing: a random code is printed to the terminal only, and the first
user who sends ``/claim <code>`` in a private chat becomes the owner. The code is
invalidated immediately and can never be reused.
"""
from __future__ import annotations

import json
import logging
import secrets
from pathlib import Path
from typing import Optional

from config import PAIRING_CODE_BYTES

_log = logging.getLogger("tgdigest.bot.owner")


class OwnerStore:
    """Loads, saves and guards the single owner id."""

    def __init__(self, path: Path, preset_owner_id: Optional[int] = None) -> None:
        self.path = Path(path)
        self._code: Optional[str] = None
        if preset_owner_id is not None:
            self._owner_id: Optional[int] = int(preset_owner_id)
        else:
            self._owner_id = self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> Optional[int]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return int(data["owner_id"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save(self, owner_id: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"owner_id": int(owner_id)}), encoding="utf-8")
        try:
            self.path.chmod(0o600)
        except OSError:  # e.g. Windows has no POSIX mode
            _log.warning("could not chmod 600 the owner file (non-POSIX filesystem?)")

    # -- api ---------------------------------------------------------------
    @property
    def owner_id(self) -> Optional[int]:
        return self._owner_id

    def is_owner(self, user_id: Optional[int]) -> bool:
        return self._owner_id is not None and user_id is not None and int(user_id) == self._owner_id

    def needs_pairing(self) -> bool:
        return self._owner_id is None

    # -- pairing -----------------------------------------------------------
    def start_pairing(self) -> str:
        """Return the one-time code (generated once). Print it to the terminal only."""
        if self._code is None:
            self._code = secrets.token_hex(PAIRING_CODE_BYTES)
        return self._code

    def claim(self, user_id: int, code: str) -> bool:
        """Try to claim ownership. On success the code is consumed forever."""
        if self._owner_id is not None:
            return False
        if not self._code:
            return False
        if not secrets.compare_digest((code or "").strip().lower(), self._code):
            return False
        self._code = None  # invalidate immediately, before anything can fail
        self._owner_id = int(user_id)
        self._save(self._owner_id)
        return True
