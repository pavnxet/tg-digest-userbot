"""Control-bot entry point.

    python bot.py

Runs until Ctrl+C. Logs to logs/bot.log. The bot client may send and delete
messages (it talks to the owner); the user client stays read-only and is only
touched by user_session.py.
"""
from __future__ import annotations

import asyncio
import contextlib
import sys

from telethon import TelegramClient, events

import config
from bot_handlers import BotApp
from channel_store import ChannelStore
from config import (
    BOT_LOG,
    BOT_SESSION_BASENAME,
    CACHE_DIR,
    CHANNELS_PATH,
    OUTPUT_DIR,
    OWNER_FILE,
    ROOT_DIR,
    RUN_HISTORY_PATH,
    RUN_LOCK_PATH,
    RUN_MAX_PER_DAY,
    RUN_MIN_INTERVAL_SECONDS,
    RUN_TIMEOUT_SECONDS,
    SCHEDULE_PATH,
    SESSION_BASENAME,
    SETTINGS_PATH,
    Credentials,
    load_bot_settings,
)
from logging_setup import setup_logging
from owner import OwnerStore
from run_manager import RunLock, RunManager
from schedule_store import ScheduleStore
from settings_store import SettingsStore
from user_session import UserSession

SWEEP_INTERVAL_SECONDS = 30


def build_bot_client(settings) -> TelegramClient:
    """The bot's own client, on its own session file (separate from the user's)."""
    return TelegramClient(BOT_SESSION_BASENAME, settings.api_id, settings.api_hash)


async def _timeout_sweeper(app: BotApp) -> None:
    """Clear a stalled login after the inactivity timeout and tell the owner."""
    while True:
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
        if app.manager.tick():
            owner_id = app.owners.owner_id
            if owner_id is not None:
                with contextlib.suppress(Exception):  # best effort
                    await app.bot.send_message(
                        owner_id, "Login timeout (5 मिनट)। State clear कर दिया। /login फिर करें।"
                    )


async def run_bot(settings=None) -> None:
    config.ensure_directories()
    log = setup_logging(BOT_LOG, verbose=True)
    settings = settings or load_bot_settings()

    owners = OwnerStore(OWNER_FILE, preset_owner_id=settings.owner_id)
    if owners.needs_pairing():
        code = owners.start_pairing()
        print("=" * 52)
        print(f"Pairing code (terminal only): {code}")
        print("Send  /claim <code>  to your bot in a private chat.")
        print("=" * 52)
    else:
        print(f"Owner configured (id ending …{str(owners.owner_id)[-2:]}).")

    credentials = Credentials(api_id=settings.api_id, api_hash=settings.api_hash, phone=settings.phone)
    bot = build_bot_client(settings)
    user = UserSession(credentials, SESSION_BASENAME)

    settings_store = SettingsStore(SETTINGS_PATH)
    schedule_store = ScheduleStore(SCHEDULE_PATH)
    run_manager = RunManager(
        main_path=ROOT_DIR / "main.py", lock_path=RUN_LOCK_PATH, history_path=RUN_HISTORY_PATH,
        python=sys.executable, timeout=RUN_TIMEOUT_SECONDS,
        min_interval=RUN_MIN_INTERVAL_SECONDS, max_per_day=RUN_MAX_PER_DAY,
        ensure_disconnected=user.disconnect,
    )
    channels_store = ChannelStore(
        CHANNELS_PATH, CACHE_DIR / "channels.lock", ids_path=CACHE_DIR / "channel_ids.json",
        is_run_active=lambda: run_manager.running,
        lock_factory=lambda path: RunLock(path, stale_after=60),
    )
    app = BotApp(bot, user, owners, log=log, channels_store=channels_store,
                 run_manager=run_manager, settings=settings_store,
                 output_dir=OUTPUT_DIR, cache_dir=CACHE_DIR, schedule_store=schedule_store)

    @bot.on(events.NewMessage)
    async def _on_message(event) -> None:  # pragma: no cover - wiring
        await app.handle(event)

    @bot.on(events.CallbackQuery)
    async def _on_callback(event) -> None:  # pragma: no cover - wiring
        await app.handle_callback(event)

    await bot.start(bot_token=settings.bot_token)
    log.info("control bot started")
    with contextlib.suppress(Exception):  # retention at startup
        app._file_panel.run_retention()
    with contextlib.suppress(Exception):  # tell the owner about missed runs
        await app._scheduler.startup_check()
    sweeper = asyncio.create_task(_timeout_sweeper(app))
    scheduler_task = asyncio.create_task(app._scheduler.run_forever())
    retention_task = asyncio.create_task(_daily_retention(app))
    try:
        await bot.run_until_disconnected()
    finally:
        for task in (sweeper, scheduler_task, retention_task):
            task.cancel()
        with contextlib.suppress(Exception):
            await user.disconnect()
        await bot.disconnect()


async def _daily_retention(app: BotApp) -> None:
    """Delete digests older than retention_days, once a day."""
    while True:
        await asyncio.sleep(86_400)
        with contextlib.suppress(Exception):
            app._file_panel.run_retention()


def main() -> None:
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\nBot stopped.")
    except (RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
