"""Fake bot and user clients plus fake events, all offline.

The user client also carries loud guards for the forbidden methods, so if the app
ever sends/deletes on the USER client the test fails immediately (belt-and-braces
on top of the AST safety scan).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Optional

from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

FORBIDDEN_USER_METHODS = [
    "send_message",
    "send_file",
    "delete_messages",
    "edit_message",
    "forward_messages",
    "send_read_acknowledge",
    "mark_read",
    "send_media",
    "send_reaction",
]


class FakeMessage:
    _counter = 0

    def __init__(self) -> None:
        FakeMessage._counter += 1
        self.id = FakeMessage._counter


class FakeBotClient:
    """The bot client. It IS allowed to send and delete (it talks to the owner)."""

    def __init__(self) -> None:
        self.sent: list[tuple] = []        # (chat_id, text, buttons, message)
        self.files: list[tuple] = []       # (chat_id, bytes, caption, message)
        self.file_meta: list[dict] = []    # what was passed to send_file (name, force_document)
        self.edits: list[tuple] = []       # (chat_id, message_id, text)
        self.deleted: list[tuple] = []     # (chat_id, ids)
        self.calls: list[tuple] = []
        self.delete_should_fail = False
        self.send_should_fail = False
        self.connected = False

    async def send_message(self, chat_id, text, buttons=None, **kwargs):
        self.calls.append(("send_message", chat_id))
        if self.send_should_fail:
            raise RuntimeError("send failed")
        message = FakeMessage()
        self.sent.append((chat_id, text, buttons, message))
        return message

    async def send_file(self, chat_id, file, caption=None, **kwargs):
        self.calls.append(("send_file", chat_id))
        if self.send_should_fail:
            raise RuntimeError("send failed")
        name = getattr(file, "name", None)
        data = file.read() if hasattr(file, "read") else file
        message = FakeMessage()
        self.files.append((chat_id, data, caption, message))
        self.file_meta.append({"name": name, "force_document": kwargs.get("force_document"), "caption": caption})
        return message

    async def edit_message(self, chat_id, message_id, text, **kwargs):
        self.calls.append(("edit_message", chat_id))
        if self.send_should_fail:
            raise RuntimeError("edit failed")
        self.edits.append((chat_id, message_id, text))
        return True

    async def delete_messages(self, chat_id, ids):
        self.calls.append(("delete_messages", chat_id))
        if self.delete_should_fail:
            raise RuntimeError("delete failed")
        self.deleted.append((chat_id, list(ids)))
        return True

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    # convenience for assertions
    def all_text(self) -> str:
        return "\n".join(t for _c, t, _b, _m in self.sent)


class FakeQRLogin:
    """Scriptable QRLogin. `script` is a list of outcomes for wait()."""

    def __init__(self, script: Optional[list] = None, url: str = "tg://login?token=AAA") -> None:
        self._script = list(script or [])
        self._url = url
        self.recreates = 0
        self.waits = 0
        self.block_event = asyncio.Event()
        self.recreate_should_fail = False

    @property
    def url(self) -> str:
        return self._url

    async def recreate(self):
        self.recreates += 1
        if self.recreate_should_fail:
            raise RuntimeError("recreate failed")
        self._url = f"tg://login?token=T{self.recreates}"

    async def wait(self, timeout: float = None):
        self.waits += 1
        step = self._script.pop(0) if self._script else ("timeout",)
        kind = step[0]
        if kind == "ok":
            return step[1] if len(step) > 1 else SimpleNamespace(first_name="Owner")
        if kind == "block":
            await self.block_event.wait()
            return SimpleNamespace(first_name="Owner")
        if kind == "timeout":
            await asyncio.sleep(0)
            raise asyncio.TimeoutError()
        if kind == "password":
            raise SessionPasswordNeededError(request=None)
        if kind == "error":
            raise step[1]
        raise asyncio.TimeoutError()


class FakeUserClient:
    """The user client. Scriptable; forbids every write method at runtime."""

    def __init__(self) -> None:
        self.authorized = False
        self.me_obj = SimpleNamespace(first_name="Owner", phone="+919812345678", id=999)
        self.qr: Optional[FakeQRLogin] = None
        self.qr_script: list = []
        self.send_code_result = SimpleNamespace(phone_code_hash="hash123")
        self.send_code_error: Optional[Exception] = None
        self.sign_in_script: list = []
        self.log_out_result = True
        self.connect_error: Optional[Exception] = None
        self.calls: list[tuple] = []
        self.connected = False
        self.entities: dict = {}
        self.invite_result = None
        self.invite_error: Optional[Exception] = None

    async def connect(self):
        self.calls.append(("connect",))
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def disconnect(self):
        self.calls.append(("disconnect",))
        self.connected = False

    async def is_user_authorized(self):
        self.calls.append(("is_user_authorized",))
        return self.authorized

    async def get_me(self):
        self.calls.append(("get_me",))
        return self.me_obj

    async def get_entity(self, key):
        self.calls.append(("get_entity", key))
        if key in self.entities:
            return self.entities[key]
        raise ValueError(f"cannot find entity {key!r}")

    async def __call__(self, request):
        name = type(request).__name__
        self.calls.append(("request", name))
        if name == "CheckChatInviteRequest":
            if self.invite_error is not None:
                raise self.invite_error
            return self.invite_result
        raise NotImplementedError(name)

    async def qr_login(self):
        self.calls.append(("qr_login",))
        if self.qr is None:
            self.qr = FakeQRLogin(self.qr_script)
        return self.qr

    async def send_code_request(self, phone):
        self.calls.append(("send_code_request", phone))
        if self.send_code_error is not None:
            raise self.send_code_error
        return self.send_code_result

    async def sign_in(self, phone=None, code=None, password=None, phone_code_hash=None):
        self.calls.append(("sign_in", {"phone": phone, "code": code, "password": password}))
        step = self.sign_in_script.pop(0) if self.sign_in_script else ("ok",)
        kind = step[0]
        if kind == "ok":
            self.authorized = True
            return SimpleNamespace(first_name="Owner")
        if kind == "password_needed":
            raise SessionPasswordNeededError(request=None)
        if kind == "code_invalid":
            raise PhoneCodeInvalidError(request=None)
        if kind == "code_expired":
            raise PhoneCodeExpiredError(request=None)
        if kind == "password_invalid":
            raise PasswordHashInvalidError(request=None)
        if kind == "number_invalid":
            raise PhoneNumberInvalidError(request=None)
        if kind == "number_banned":
            raise PhoneNumberBannedError(request=None)
        if kind == "flood":
            from telethon.errors import FloodWaitError

            raise FloodWaitError(request=None, capture=step[1] if len(step) > 1 else 42)
        if kind == "error":
            raise step[1]
        raise RuntimeError("unknown step")

    async def log_out(self):
        self.calls.append(("log_out",))
        return self.log_out_result


def _forbidden(name):
    async def method(self, *args, **kwargs):
        raise AssertionError(f"USER client must never call {name}()!")
    return method


for _name in FORBIDDEN_USER_METHODS:
    setattr(FakeUserClient, _name, _forbidden(_name))


class FakeEvent:
    """A private message from the owner (or anyone)."""

    def __init__(self, bot: FakeBotClient, text: str, sender_id: int,
                 chat_id: Optional[int] = None, is_private: bool = True,
                 message_id: int = 1) -> None:
        self.bot = bot
        self.text = text
        self.sender_id = sender_id
        self.chat_id = sender_id if chat_id is None else chat_id
        self.is_private = is_private
        self.message = SimpleNamespace(id=message_id)
        self.replies: list[tuple] = []
        self.parse_modes: list = []
        self.deleted = False

    async def respond(self, text, buttons=None, parse_mode=None, **kwargs):
        self.replies.append((text, buttons))
        self.parse_modes.append(parse_mode)
        return await self.bot.send_message(self.chat_id, text, buttons=buttons)

    async def delete(self):
        self.deleted = True
        return await self.bot.delete_messages(self.chat_id, [self.message.id])


class FakeCallbackEvent:
    """An inline-button press."""

    def __init__(self, bot: FakeBotClient, data: bytes, sender_id: int,
                 chat_id: Optional[int] = None, is_private: bool = True) -> None:
        self.bot = bot
        self.data = data
        self.sender_id = sender_id
        self.chat_id = sender_id if chat_id is None else chat_id
        self.is_private = is_private
        self.replies: list[tuple] = []
        self.edits: list[tuple] = []
        self.answered = False

    async def answer(self, *args, **kwargs):
        self.answered = True

    async def edit(self, text=None, buttons=None, parse_mode=None, **kwargs):
        """Edit the message the button belongs to (Telethon's CallbackQuery.edit)."""
        self.edits.append((text, buttons))
        return await self.bot.send_message(self.chat_id, text, buttons=buttons)

    async def respond(self, text, buttons=None, parse_mode=None, **kwargs):
        self.replies.append((text, buttons))
        return await self.bot.send_message(self.chat_id, text, buttons=buttons)


def build_app(tmp_path, *, owner_id: int = 555, needs_pairing: bool = False, log: Any = None,
              channels_text: Optional[str] = None, run_script=None,
              output_dir=None, cache_dir=None, min_interval: int = 0):
    """Wire a BotApp with fakes. Returns (app, bot, user_client, owners, session).

    `app.user` is a REAL UserSession wrapping the fake user client, so the
    session-file and locked-session logic are exercised too. Pass
    `channels_text` to also attach a real ChannelStore (as `app.channels_store`).
    """
    from bot_handlers import BotApp
    from channel_store import ChannelStore
    from config import Credentials
    from owner import OwnerStore
    from user_session import UserSession

    owners = OwnerStore(tmp_path / "owner.json", preset_owner_id=None if needs_pairing else owner_id)
    bot = FakeBotClient()
    fake_user = FakeUserClient()
    credentials = Credentials(api_id=1, api_hash="hash", phone="")
    session = UserSession(credentials, str(tmp_path / "tg_digest"), client_factory=lambda c, b: fake_user)
    store = None
    if channels_text is not None:
        (tmp_path / "channels.txt").write_text(channels_text, encoding="utf-8")
        store = ChannelStore(tmp_path / "channels.txt", tmp_path / "channels.lock")
    run_manager = None
    settings = None
    schedule_store = None
    if run_script is not None:
        import sys

        from run_manager import RunManager
        from settings_store import SettingsStore

        output_dir = output_dir or (tmp_path / "output")
        cache_dir = cache_dir or (tmp_path / "cache")
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        settings = SettingsStore(tmp_path / "settings.json")
        run_manager = RunManager(
            main_path=run_script, lock_path=Path(cache_dir) / "run.lock",
            history_path=Path(cache_dir) / "history.json", python=sys.executable,
            timeout=30, min_interval=min_interval, max_per_day=100,
            ensure_disconnected=session.disconnect,
        )
        from schedule_store import ScheduleStore

        schedule_store = ScheduleStore(tmp_path / "schedule.json")
    app = BotApp(bot, session, owners, log=log, channels_store=store,
                 run_manager=run_manager, settings=settings,
                 output_dir=output_dir, cache_dir=cache_dir,
                 schedule_store=schedule_store)
    return app, bot, fake_user, owners, session
