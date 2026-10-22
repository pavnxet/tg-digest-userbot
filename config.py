"""Configuration: environment credentials, paths and default constants.

Nothing here touches the network. `load_credentials()` is the only function
that reads the environment, and it never hardcodes secrets.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Format checks, so a truncated hash or a typo'd token fails immediately with a
# message that points at the right .env line instead of a cryptic Telethon error.
_API_HASH_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_PHONE_RE = re.compile(r"^\+?[1-9]\d{6,14}$")
_BOT_TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")

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

# Control bot (created via @BotFather). Its session is separate from the user's.
BOT_SESSION_BASENAME: str = str(ROOT_DIR / "bot")
OWNER_FILE: Path = ROOT_DIR / "owner.json"
BOT_LOG: Path = LOG_DIR / "bot.log"
PAIRING_CODE_BYTES: int = 4  # -> 8 hex characters, printed to the terminal only

# Control-plane run limits (section A). All overridable from the environment later.
RUN_TIMEOUT_SECONDS: int = 1800        # hard timeout for one scrape (30 min)
HISTORY_TIMEOUT_SECONDS: int = 7200    # a full-history scrape gets 2 hours
HISTORY_MAX_FLOOD_WAIT: int = 3600     # tolerate long flood waits mid-history
RUN_MIN_INTERVAL_SECONDS: int = 300    # minimum gap between two runs (5 min)
RUN_MAX_PER_DAY: int = 12              # maximum runs per IST day (manual + scheduled)
RUN_LOCK_PATH: Path = CACHE_DIR / "run.lock"
RUN_HISTORY_PATH: Path = CACHE_DIR / "run_history.json"
SETTINGS_PATH: Path = ROOT_DIR / "settings.json"
SCHEDULE_PATH: Path = ROOT_DIR / "schedule.json"

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
    "A channel marked truncated:true hit a per-channel cap, so its messages may be incomplete. "
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
    """Load API_ID / API_HASH from a .env file. PHONE is optional.

    PHONE is only needed for the *terminal* phone login (``--terminal-login``).
    If you logged in with a QR code, the saved session is enough, so PHONE is
    not required here. Raises a clear RuntimeError if API_ID / API_HASH are
    missing, so the user is told exactly what to fix.
    """
    load_dotenv(env_path)

    raw_id = (os.getenv("API_ID") or "").strip()
    api_hash = (os.getenv("API_HASH") or "").strip()
    phone = (os.getenv("PHONE") or "").strip()

    missing = [
        name
        for name, value in (("API_ID", raw_id), ("API_HASH", api_hash))
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

    if not _API_HASH_RE.match(api_hash):
        raise RuntimeError(
            f"API_HASH in {env_path} must be 32 hex characters. Copy it exactly from "
            "my.telegram.org → API development tools (see .env.example)."
        )
    if phone and not _PHONE_RE.match(phone):
        raise RuntimeError(
            f"PHONE in {env_path} must be in E.164 form, e.g. +919812345678 (see .env.example). "
            "Leave it blank if you log in with a QR code."
        )

    return Credentials(api_id=api_id, api_hash=api_hash, phone=phone)


def ensure_directories() -> None:
    """Create the runtime directories if they do not exist yet."""
    for directory in (OUTPUT_DIR, CACHE_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class BotSettings:
    """Everything the control bot needs from the environment.

    `owner_id` is optional: when absent the bot falls back to one-time pairing.
    """

    api_id: int
    api_hash: str
    bot_token: str
    owner_id: "int | None" = None
    phone: str = ""


def load_bot_settings(env_path: Path = ENV_PATH) -> BotSettings:
    """Load API_ID / API_HASH / BOT_TOKEN (and optional OWNER_ID) from .env."""
    load_dotenv(env_path)

    raw_id = (os.getenv("API_ID") or "").strip()
    api_hash = (os.getenv("API_HASH") or "").strip()
    bot_token = (os.getenv("BOT_TOKEN") or "").strip()
    raw_owner = (os.getenv("OWNER_ID") or "").strip()
    phone = (os.getenv("PHONE") or "").strip()

    missing = [
        name
        for name, value in (("API_ID", raw_id), ("API_HASH", api_hash), ("BOT_TOKEN", bot_token))
        if not value
    ]
    if missing:
        raise RuntimeError(
            f"Missing {', '.join(missing)} in {env_path}. "
            "Copy .env.example to .env and fill in your credentials."
        )

    try:
        api_id = int(raw_id)
    except ValueError as exc:
        raise RuntimeError("API_ID must be an integer (e.g. 1234567).") from exc

    if not _API_HASH_RE.match(api_hash):
        raise RuntimeError(
            f"API_HASH in {env_path} must be 32 hex characters (see .env.example)."
        )
    if not _BOT_TOKEN_RE.match(bot_token):
        raise RuntimeError(
            f"BOT_TOKEN in {env_path} looks wrong (see .env.example). "
            "Get a fresh one from @BotFather with /token."
        )

    owner_id = None
    if raw_owner:
        try:
            owner_id = int(raw_owner)
        except ValueError as exc:
            raise RuntimeError("OWNER_ID must be a numeric Telegram user id.") from exc

    return BotSettings(api_id=api_id, api_hash=api_hash, bot_token=bot_token, owner_id=owner_id, phone=phone)
