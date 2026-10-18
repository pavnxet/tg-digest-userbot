"""Configuration: environment credentials, paths and default constants.

Nothing here touches the network. `load_credentials()` is the only function
that reads the environment, and it never hardcodes secrets.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# --------------------------------------------------------------------------
# Paths (all relative to the repository root, i.e. this file's directory)
# --------------------------------------------------------------------------
ROOT_DIR: Path = Path(__file__).resolve().parent
ENV_PATH: Path = ROOT_DIR / ".env"
CHANNELS_PATH: Path = ROOT_DIR / "channels.txt"
STATE_PATH: Path = ROOT_DIR / "state.json"
OUTPUT_DIR: Path = ROOT_DIR / "output"
CACHE_DIR: Path = ROOT_DIR / ".cache"
LOG_DIR: Path = ROOT_DIR / "logs"

# Telethon appends ".session" itself, so this is the basename, not a file.
SESSION_BASENAME: str = str(ROOT_DIR / "tg_digest")

# --------------------------------------------------------------------------
# Defaults (all overridable from the command line)
# --------------------------------------------------------------------------
DEFAULT_TIMEZONE: str = "Asia/Kolkata"
DEFAULT_MAX_PER_CHANNEL: int = 5000
DEFAULT_MAX_JOINS: int = 5
DEFAULT_MAX_CHARS: int = 300_000
DEFAULT_MAX_FLOOD_WAIT: int = 900
DEFAULT_MIN_CHARS: int = 0
JOIN_WAIT_MIN_SECONDS: int = 30
JOIN_WAIT_MAX_SECONDS: int = 60
CHANNEL_WAIT_MIN_SECONDS: float = 1.0
CHANNEL_WAIT_MAX_SECONDS: float = 3.0

# Default prompt handed to whichever AI the user pastes the file into.
DEFAULT_AI_INSTRUCTIONS: str = (
    "This file is JSON containing messages from several Telegram channels. "
    "Each entry in channels[].messages is one message, and `t` is the time in IST. "
    "The link to a message is the channel's link_base plus the message id. Please: "
    "(1) give a short, separate summary for each channel; "
    "(2) list the 10 most important points across all channels; "
    "(3) list deadlines, dates and action items separately; "
    "(4) if the same news appears in several channels, mention it once and say "
    "which channels carried it. "
    "Do not add anything that is not in the messages, and flag anything you are "
    "unsure about. Respond in Hindi."
)


@dataclass(frozen=True)
class Credentials:
    """Telegram API credentials loaded from the environment."""

    api_id: int
    api_hash: str
    phone: str


def load_credentials(env_path: Path = ENV_PATH) -> Credentials:
    """Load API_ID / API_HASH / PHONE from a .env file.

    Raises a clear RuntimeError if anything is missing so the user is told
    exactly what to fix, rather than getting an obscure Telethon error.
    """
    load_dotenv(env_path)

    raw_id = (os.getenv("API_ID") or "").strip()
    api_hash = (os.getenv("API_HASH") or "").strip()
    phone = (os.getenv("PHONE") or "").strip()

    missing = [
        name
        for name, value in (("API_ID", raw_id), ("API_HASH", api_hash), ("PHONE", phone))
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing {', '.join(missing)} in {env_path}. "
            "Copy .env.example to .env and fill in your credentials."
        )

    try:
        api_id = int(raw_id)
    except ValueError as exc:  # pragma: no cover - trivial guard
        raise RuntimeError("API_ID must be an integer (e.g. 1234567).") from exc

    return Credentials(api_id=api_id, api_hash=api_hash, phone=phone)


def ensure_directories() -> None:
    """Create the runtime directories if they do not exist yet."""
    for directory in (OUTPUT_DIR, CACHE_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
