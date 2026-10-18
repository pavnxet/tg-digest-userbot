"""Telethon client construction and first-time login.

The client is read-only by contract: no send, no reaction, no read receipts.
That is enforced by simply never calling those methods anywhere in the code.
"""
from __future__ import annotations

from pathlib import Path

from telethon import TelegramClient

from config import Credentials


def build_client(credentials: Credentials, session_basename: str) -> TelegramClient:
    """Create a TelegramClient bound to a local .session file."""
    return TelegramClient(str(session_basename), credentials.api_id, credentials.api_hash)


async def login(client: TelegramClient, phone: str = "") -> None:
    """Start the client, prompting for OTP and 2FA password if needed.

    Telethon's `start()` handles the interactive prompts in the terminal and
    persists the session so later runs skip login entirely. If `phone` is empty
    (e.g. a QR-login setup with no PHONE in .env) the argument is omitted so
    Telethon prompts for the number -- passing ``phone=None`` explicitly would
    raise ``ValueError: No phone number or bot token provided``.
    """
    if phone:
        await client.start(phone=phone)
    else:
        await client.start()


def session_file_exists(session_basename: str) -> bool:
    """True if a .session file already exists (i.e. we are already logged in)."""
    return Path(f"{session_basename}.session").exists()
