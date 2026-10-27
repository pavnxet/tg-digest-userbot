"""Control-bot command handlers, behind a single owner guard.

Every command is reachable only through ``BotApp.handle`` (messages) or
``BotApp.handle_callback`` (inline buttons). Both check the owner first, so a new
handler cannot forget the check. Non-owner updates are dropped with no reply and
nothing about their content is logged.

Sensitive input (phone, login code, 2FA password, /claim) is deleted from the
chat immediately after it is read, and never logged or stored beyond the flow.
"""
from __future__ import annotations

import asyncio
import contextlib
import difflib
import io
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.tl.custom import Button

# Help text and the per-command guides live in bot_help.py, formatted for
# Telegram's HTML parse mode. Re-exported here so the panels and tests keep
# importing them from this module.
from bot_help import COMMAND_GUIDES, GUIDE_COMMANDS, HELP_TEXT, esc, guide_for
from login_state import (
    MAX_CODE_ATTEMPTS,
    MAX_PASSWORD_ATTEMPTS,
    QR_MAX_REFRESHES,
    LoginManager,
)
from user_session import SessionLockedError

_log = logging.getLogger("tgdigest.bot")

QR_MAX_SENDS = QR_MAX_REFRESHES + 1      # initial QR + up to 3 refreshes


@dataclass
class Pending:
    """A confirmation awaiting a button press (expires after 60s)."""

    action: str
    payload: dict = field(default_factory=dict)
    expires_at: float = 0.0


SEND_CODE_PER_HOUR = 3
_NON_DIGITS = re.compile(r"[^0-9]")
_NON_PHONE = re.compile(r"[^0-9+]")
_PHONE_RE = re.compile(r"^\+?[1-9]\d{6,14}$")

# The bot's command menu (Telegram shows this next to the input box).
# Telegram limits: name 1-32 chars, no leading slash; description 3-256 chars.
COMMAND_MENU = (
    ("start", "शुरू / start"),
    ("help", "मदद / help"),
    ("login", "QR से लॉगिन"),
    ("login_phone", "फ़ोन + कोड से लॉगिन"),
    ("status", "लॉगिन स्थिति"),
    ("logout", "लॉगआउट"),
    ("cancel", "रद्द करें"),
    ("channels", "चैनल सूची"),
    ("add", "चैनल जोड़ें"),
    ("remove", "चैनल हटाएँ"),
    ("pause", "चैनल pause"),
    ("resume", "चैनल resume"),
    ("tag", "tag जोड़ें"),
    ("untag", "tag हटाएँ"),
    ("groups", "tag groups"),
    ("run", "scraper चलाएँ"),
    ("history", "पूरी history scrape"),
    ("stop", "run रोकें"),
    ("preview", "dry-run preview"),
    ("last", "पिछला digest भेजें"),
    ("schedule", "scheduled runs"),
    ("settings", "सेटिंग्स देखें"),
    ("set", "सेटिंग बदलें"),
    ("prompts", "prompt सूची"),
    ("prompt", "prompt दिखाएँ"),
    ("files", "outputs सूची"),
    ("clean", "पुरानी files हटाएँ"),
    ("find", "digest में खोजें"),
    ("health", "health जाँच"),
    ("panic", "kill switch"),
)


def render_qr_png(url: str) -> bytes:
    """Render a QR PNG in memory. No file is ever written to disk."""
    import qrcode

    image = qrcode.make(url)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class BotApp:
    """Holds the bot client, the user session and the login state."""

    def __init__(
        self,
        bot: Any,
        user_session: Any,
        owner_store: Any,
        manager: Optional[LoginManager] = None,
        log: Optional[logging.Logger] = None,
        channels_store: Any = None,
        run_manager: Any = None,
        settings: Any = None,
        output_dir: Any = None,
        cache_dir: Any = None,
        schedule_store: Any = None,
    ) -> None:
        self.bot = bot
        self.user = user_session
        self.owners = owner_store
        self.manager = manager or LoginManager()
        self.log = log or _log
        self.channels_store = channels_store
        # Panels use this for small caches (e.g. resolved channel titles).
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self._code_times: list[float] = []
        self._pending: dict[str, Pending] = {}
        self._confirm_actions: dict[str, Any] = {}
        self._extra_callbacks: dict[bytes, Any] = {}
        self._prefix_callbacks: dict[bytes, Any] = {}
        # The single command registry. Handlers are reachable only through the
        # guarded dispatcher below, so adding one here is automatically guarded.
        self.commands = {
            "/start": self.cmd_start,
            "/help": self.cmd_start,
            "/login": self.cmd_login,
            "/login_phone": self.cmd_login_phone,
            "/status": self.cmd_status,
            "/logout": self.cmd_logout,
            "/cancel": self.cmd_cancel,
        }
        if channels_store is not None:
            from bot_channels import ChannelPanel

            self._channel_panel = ChannelPanel(self)
        if run_manager is not None and settings is not None:
            from bot_run import RunPanel

            self._run_panel = RunPanel(self, run_manager, settings, output_dir, cache_dir)
        if settings is not None:
            from bot_settings import SettingsPanel

            self._settings_panel = SettingsPanel(self, settings)
        if settings is not None:
            from bot_prompts import PromptPanel

            self._prompt_panel = PromptPanel(self, settings)
        if schedule_store is not None:
            from bot_schedule import SchedulePanel, Scheduler

            self._schedule_panel = SchedulePanel(self, schedule_store)
            if run_manager is not None:
                self._scheduler = Scheduler(self, schedule_store, self._run_panel)
        if output_dir is not None and settings is not None:
            from bot_files import FilePanel

            self._file_panel = FilePanel(self, output_dir, settings)
        if run_manager is not None:
            from bot_health import HealthPanel

            self._health_panel = HealthPanel(self, run_manager, output_dir, cache_dir, schedule_store)

    # ------------------------------------------------------------------ guard
    async def handle(self, event: Any) -> None:
        """Message entry point. Owner-checked; groups/channels ignored."""
        if not getattr(event, "is_private", False):
            return
        text = (getattr(event, "text", None) or "").strip()
        command = text.split()[0].lower() if text.startswith("/") else None
        sender_id = getattr(event, "sender_id", None)

        # Pairing is the only thing allowed before an owner exists.
        if self.owners.needs_pairing():
            if command == "/claim":
                await self._handle_claim(event, text)
            return

        if not self.owners.is_owner(sender_id):
            return  # silent: no reply, no content logged

        if self.manager.tick():
            await self._respond(event, "पिछला login timeout हो गया। / login timed out. /login फिर से करें।")

        if command in self.commands:
            # A command that needs arguments, sent bare (which is what tapping it
            # in the Telegram menu does), shows its full guide instead of a
            # terse usage line.
            bare = not text.partition(" ")[2].strip()
            name = command.lstrip("/")
            if bare and name in GUIDE_COMMANDS:
                await self._respond(event, COMMAND_GUIDES[name], parse_mode="html")
                return
            await self.commands[command](event)
            return

        if self.manager.active and self.manager.state.awaiting:
            await self._handle_input(event)
            return

        # Suggest the closest real command instead of a dead end.
        if command:
            known = [name.lstrip("/") for name in self.commands]
            guess = difflib.get_close_matches(command.lstrip("/").lower(), known, n=1, cutoff=0.6)
            if guess:
                await self._respond(
                    event, f"«{command}» नहीं पहचाना। क्या आप /{guess[0]} चाहते थे?\nसभी commands: /help",
                )
                return
        await self._respond(event, "समझ नहीं आया। सभी commands की सूची के लिए /help भेजें।")

    async def handle_callback(self, event: Any) -> None:
        """Inline-button entry point. Owner-checked too."""
        if not getattr(event, "is_private", False):
            return
        if not self.owners.is_owner(getattr(event, "sender_id", None)):
            return
        with contextlib.suppress(Exception):  # answering is best-effort
            await event.answer()
        data = getattr(event, "data", b"") or b""
        if data == b"logout_yes":
            await self._do_logout(event)
        elif data == b"logout_no":
            await self._respond(event, "रद्द किया गया। / Cancelled.")
        elif data.startswith(b"confirm:") or data.startswith(b"cancel:"):
            await self._handle_confirmation(event, data)
        else:
            handler = self._extra_callbacks.get(data)
            if handler is not None:
                await handler(event)
                return
            for prefix, prefix_handler in self._prefix_callbacks.items():
                if data.startswith(prefix):
                    await prefix_handler(event, data[len(prefix):])
                    return
            # A button from an old message (or an unknown payload): say so instead
            # of doing nothing at all.
            await self._respond(event, "यह button पुराना है। /help देखें।")

    # -- generic confirmations (destructive actions) -----------------------
    def register_confirmation(self, action: str, handler: Any) -> None:
        self._confirm_actions[action] = handler

    def register_callback(self, data: bytes, handler: Any) -> None:
        self._extra_callbacks[data] = handler

    def register_callback_prefix(self, prefix: bytes, handler: Any) -> None:
        """Handle every callback whose data starts with `prefix` (rest is passed on)."""
        self._prefix_callbacks[prefix] = handler

    async def ask_confirmation(self, event: Any, text: str, action: str,
                               payload: Optional[dict] = None,
                               confirm_label: str = "हाँ / Yes",
                               cancel_label: str = "रद्द / Cancel") -> None:
        token = secrets.token_urlsafe(8)
        self._pending[token] = Pending(action=action, payload=payload or {}, expires_at=time.time() + 60)
        buttons = [[Button.inline(confirm_label, f"confirm:{token}".encode()),
                    Button.inline(cancel_label, f"cancel:{token}".encode())]]
        await self._respond(event, text, buttons=buttons)

    async def _handle_confirmation(self, event: Any, data: bytes) -> None:
        kind, _, token = data.decode("utf-8").partition(":")
        pending = self._pending.pop(token, None)
        if pending is None:
            await self._respond(event, "यह confirmation पहले ही इस्तेमाल हो चुका है।")
            return
        if time.time() > pending.expires_at:
            await self._respond(event, "confirmation expire हो गया (60s)। फिर से भेजें।")
            return
        if kind == "cancel":
            await self._respond(event, "रद्द किया गया। / Cancelled.")
            return
        handler = self._confirm_actions.get(pending.action)
        if handler is None:
            await self._respond(event, "यह action उपलब्ध नहीं है।")
            return
        await handler(event, pending.payload)

    # --------------------------------------------------------------- pairing
    async def _handle_claim(self, event: Any, text: str) -> None:
        parts = text.split()
        code = parts[1] if len(parts) > 1 else ""
        ok = await self._delete_incoming(event)
        if not ok:
            self.log.warning("could not delete a /claim message")
        if self.owners.claim(getattr(event, "sender_id", 0), code):
            self.log.info("owner paired successfully")  # no id, no code
            await self._respond(event, "✅ Paired. अब आप owner हैं। /help देखें।")

    # -------------------------------------------------------------- commands
    async def cmd_start(self, event: Any) -> None:
        arg = (getattr(event, "text", "") or "").partition(" ")[2].strip()
        await self._send_help(event, arg)

    async def _send_help(self, event: Any, arg: str) -> None:
        """The command list, or one command's full guide (/help <command>)."""
        if not arg:
            await self._respond(event, HELP_TEXT, parse_mode="html")
            return
        guide = guide_for(arg)
        if guide is None:
            await self._respond(
                event,
                f"«{esc(arg)}» नाम का कोई command नहीं है। /help देखें।",
                parse_mode="html",
            )
            return
        await self._respond(event, guide, parse_mode="html")

    async def cmd_login(self, event: Any) -> None:
        """QR login (default)."""
        if self.manager.active:
            await self._respond(event, "एक login पहले से चल रहा है। /cancel करें।")
            return
        try:
            await self.user.connect()
        except SessionLockedError:
            await self._respond(event, "Session file अभी locked है (scraper चल रहा होगा?)। बाद में कोशिश करें।")
            return
        except Exception:  # noqa: BLE001 - never crash on an unexpected connect error
            await self._respond(event, "User client connect नहीं हो सका। बाद में कोशिश करें।")
            return
        try:
            if await self.user.is_authorized():
                await self._respond(event, "पहले से logged in हैं। /status देखें।")
                await self.user.disconnect()
                return
            self.manager.begin("qr")
            qr = await self.user.qr_login()
        except Exception:  # noqa: BLE001
            self.manager.clear()
            await self.user.disconnect()
            await self._respond(event, "QR login शुरू नहीं हो सका। बाद में कोशिश करें।")
            return
        self.manager.state.qr = qr
        await self._send_qr(event, qr)
        self.manager.state.qr_task = asyncio.create_task(self._qr_wait(event))

    async def cmd_login_phone(self, event: Any) -> None:
        if self.manager.active:
            await self._respond(event, "एक login पहले से चल रहा है। /cancel करें।")
            return
        try:
            await self.user.connect()
        except SessionLockedError:
            await self._respond(event, "Session file अभी locked है। बाद में कोशिश करें।")
            return
        except Exception:  # noqa: BLE001 - never crash on an unexpected connect error
            await self._respond(event, "User client connect नहीं हो सका। बाद में कोशिश करें।")
            return
        try:
            if await self.user.is_authorized():
                await self._respond(event, "पहले से logged in हैं। /status देखें।")
                await self.user.disconnect()
                return
        except Exception:  # noqa: BLE001
            await self.user.disconnect()
            await self._respond(event, "Session check fail हो गया।")
            return
        state = self.manager.begin("phone")
        state.awaiting = "phone"
        await self._respond(event, "फ़ोन नंबर international format में भेजें, जैसे +91XXXXXXXXXX।\n"
                                   "मैं इसे तुरंत delete कर दूँगा।")

    async def cmd_status(self, event: Any) -> None:
        try:
            await self.user.connect()
        except SessionLockedError:
            await self._respond(event, "Session अभी locked है (scraper चल रहा होगा?)। थोड़ी देर बाद देखें।")
            return
        except Exception:  # noqa: BLE001 - never crash on an unexpected connect error
            await self._respond(event, "User client connect नहीं हो सका। बाद में कोशिश करें।")
            return
        try:
            if await self.user.is_authorized():
                me = await self.user.me()
                name = getattr(me, "first_name", None) or "?"
                phone = getattr(me, "phone", None) or ""
                tail = phone[-2:] if len(phone) >= 2 else ""
                await self._respond(event, f"✅ Logged in: {name} (…{tail})")
            else:
                await self._respond(event, "❌ Logged in नहीं हैं। /login करें।")
        except Exception:  # noqa: BLE001
            await self._respond(event, "Status check fail हो गया।")
        finally:
            await self.user.disconnect()

    async def cmd_logout(self, event: Any) -> None:
        if self.manager.active:
            await self._respond(event, "पहले /cancel करें।")
            return
        try:
            await self.user.connect()
        except SessionLockedError:
            await self._respond(event, "Session अभी locked है। बाद में कोशिश करें।")
            return
        except Exception:  # noqa: BLE001 - never crash on an unexpected connect error
            await self._respond(event, "User client connect नहीं हो सका। बाद में कोशिश करें।")
            return
        try:
            authorized = await self.user.is_authorized()
        except Exception:  # noqa: BLE001
            authorized = False
        finally:
            await self.user.disconnect()
        if not authorized:
            await self._respond(event, "पहले से logged out हैं।")
            return
        buttons = [[Button.inline("हाँ, logout / Yes", b"logout_yes"),
                    Button.inline("रद्द / Cancel", b"logout_no")]]
        await self._respond(event, "पक्का logout करना है? / Confirm logout?", buttons=buttons)

    async def cmd_cancel(self, event: Any) -> None:
        await self._delete_qr_message(event)
        self.manager.clear()
        with contextlib.suppress(Exception):
            await self.user.disconnect()
        await self._respond(event, "रद्द कर दिया। / Cancelled.")

    # ---------------------------------------------------------------- inputs
    async def _handle_input(self, event: Any) -> None:
        state = self.manager.state
        self.manager.touch()
        if state.awaiting == "phone":
            await self._step_phone(event)
        elif state.awaiting == "code":
            await self._step_code(event)
        elif state.awaiting == "password":
            await self._step_password(event)

    async def _step_phone(self, event: Any) -> None:
        raw = await self._consume_secret(event)
        phone = _NON_PHONE.sub("", raw)
        if not _PHONE_RE.match(phone):
            await self._respond(event, "फ़ोन नंबर सही नहीं लगा। international format में भेजें, जैसे +91XXXXXXXXXX।")
            return
        if not self._code_allowed():
            await self._respond(event, "बहुत ज़्यादा code requests (3/घंटा)। एक घंटे बाद कोशिश करें।")
            return
        try:
            sent = await self.user.send_code_request(phone)
        except FloodWaitError as exc:
            await self._respond(event, f"Telegram ने {exc.seconds} सेकंड रोका है। बाद में कोशिश करें।")
            await self._end_flow(event)
            return
        except PhoneNumberInvalidError:
            await self._respond(event, "यह फ़ोन नंबर invalid है।")
            return
        except PhoneNumberBannedError:
            await self._respond(event, "यह नंबर banned है।")
            await self._end_flow(event)
            return
        except Exception:  # noqa: BLE001
            await self._respond(event, "Code request fail हो गया।")
            await self._end_flow(event)
            return
        self._code_times.append(time.time())
        state = self.manager.state
        state.phone = phone
        state.phone_code_hash = getattr(sent, "phone_code_hash", None)
        state.awaiting = "code"
        state.code_attempts = 0
        await self._respond(event, "Code आपके Telegram app में आया है (SMS नहीं)।\n"
                                   "अंकों के बीच separator डालें, जैसे 1-2-3-4-5।")

    async def _step_code(self, event: Any) -> None:
        raw = await self._consume_secret(event)
        code = _NON_DIGITS.sub("", raw)
        state = self.manager.state
        if not code:
            await self._respond(event, "Code खाली था। फिर भेजें।")
            return
        try:
            await self.user.sign_in(phone=state.phone, code=code, phone_code_hash=state.phone_code_hash)
        except SessionPasswordNeededError:
            state.flow = "2fa"
            state.awaiting = "password"
            state.password_attempts = 0
            await self._respond(event, "2FA password भेजें। (सबसे सुरक्षित: terminal login)")
            return
        except PhoneCodeInvalidError:
            state.code_attempts += 1
            if state.code_attempts >= MAX_CODE_ATTEMPTS:
                self._note_possible_chat_block()
                await self._end_flow(event, "Code गलत (3 बार)। Telegram chat से भेजा code block कर सकता है — "
                                            "/login (QR) या terminal login आज़माएँ।")
            else:
                await self._respond(event, f"Code गलत है। फिर भेजें ({state.code_attempts}/{MAX_CODE_ATTEMPTS}).")
            return
        except PhoneCodeExpiredError:
            self._note_possible_chat_block()
            await self._end_flow(event, "Code expire हो गया। Telegram ने chat से भेजा code block किया हो सकता है — "
                                        "/login (QR) या terminal login आज़माएँ।")
            return
        except FloodWaitError as exc:
            await self._respond(event, f"Telegram ने {exc.seconds} सेकंड रोका है। बाद में कोशिश करें।")
            await self._end_flow(event)
            return
        except Exception:  # noqa: BLE001
            await self._respond(event, "Sign-in fail हो गया।")
            await self._end_flow(event)
            return
        await self._on_login_success(event)

    async def _step_password(self, event: Any) -> None:
        password = await self._consume_secret(event)
        state = self.manager.state
        try:
            await self.user.sign_in(password=password)
        except PasswordHashInvalidError:
            state.password_attempts += 1
            if state.password_attempts >= MAX_PASSWORD_ATTEMPTS:
                await self._end_flow(event, "गलत 2FA password (3 बार)।")
            else:
                await self._respond(event, f"गलत password। फिर भेजें ({state.password_attempts}/{MAX_PASSWORD_ATTEMPTS}).")
            return
        except FloodWaitError as exc:
            await self._respond(event, f"Telegram ने {exc.seconds} सेकंड रोका है।")
            await self._end_flow(event)
            return
        except Exception:  # noqa: BLE001
            await self._respond(event, "2FA fail हो गया।")
            await self._end_flow(event)
            return
        finally:
            del password  # drop the secret as soon as it is used
        await self._on_login_success(event)

    # ------------------------------------------------------------------- QR
    async def _send_qr(self, event: Any, qr: Any) -> None:
        try:
            png = render_qr_png(qr.url)
            caption = ("QR scan करें: दूसरे device के Telegram में Settings > Devices > "
                       "Link Desktop Device.\nयह code जल्दी expire होता है।")
            # Telethon decides "photo vs document" from the filename, so bare bytes
            # (no name, no extension) go out as an application file. Give the PNG a
            # name and ask for a photo so Telegram renders it inline.
            image = io.BytesIO(png)
            image.name = "login-qr.png"
            message = await self.bot.send_file(
                event.chat_id, image, caption=caption, force_document=False,
            )
            self.manager.state.qr_message_id = getattr(message, "id", None)
        except Exception as exc:  # noqa: BLE001 - never fail the login flow here
            # Include the reason: a missing dependency (e.g. Pillow) shows up here.
            self.log.warning("could not send the QR image: %s: %s", type(exc).__name__, exc)
        await self._respond(event, "या यह link खोलें / or open this link:\n" + qr.url)

    async def _qr_wait(self, event: Any) -> None:
        state = self.manager.state
        while True:
            try:
                await state.qr.wait()
            except asyncio.TimeoutError:
                if state.qr_refreshes >= QR_MAX_REFRESHES:
                    await self._end_flow(event, "QR बार-बार expire हो गया। बाद में /login फिर कोशिश करें।")
                    return
                state.qr_refreshes += 1
                await self._delete_qr_message(event)
                try:
                    await state.qr.recreate()
                except Exception:  # noqa: BLE001
                    await self._end_flow(event, "QR refresh नहीं हो सका।")
                    return
                await self._send_qr(event, state.qr)
                continue
            except SessionPasswordNeededError:
                state.flow = "2fa"
                state.awaiting = "password"
                state.password_attempts = 0
                state.qr_task = None
                await self._delete_qr_message(event)
                await self._respond(event, "2FA password भेजें। (सबसे सुरक्षित: terminal login)")
                return
            except asyncio.CancelledError:
                return
            except Exception:  # noqa: BLE001
                await self._end_flow(event, "Login पूरा नहीं हो सका।")
                return
            await self._on_login_success(event)
            return

    async def _delete_qr_message(self, event: Any) -> None:
        message_id = self.manager.state.qr_message_id
        if message_id is None:
            return
        try:
            await self.bot.delete_messages(event.chat_id, [message_id])
        except Exception:  # noqa: BLE001
            self.log.warning("could not delete a QR message")
        self.manager.state.qr_message_id = None

    async def _on_login_success(self, event: Any) -> None:
        try:
            me = await self.user.me()
            name = getattr(me, "first_name", None) or "?"
        except Exception:  # noqa: BLE001
            name = "?"
        await self._end_flow(event, f"✅ Login हो गया। / Logged in as {name}.")

    async def _end_flow(self, event: Any, message: Optional[str] = None) -> None:
        await self._delete_qr_message(event)
        self.manager.clear()
        with contextlib.suppress(Exception):
            await self.user.disconnect()
        if message:
            await self._respond(event, message)

    # --------------------------------------------------------------- logout
    async def _do_logout(self, event: Any) -> None:
        try:
            await self.user.connect()
        except SessionLockedError:
            await self._respond(event, "Session locked। बाद में कोशिश करें।")
            return
        except Exception:  # noqa: BLE001 - never crash on an unexpected connect error
            await self._respond(event, "User client connect नहीं हो सका। बाद में कोशिश करें।")
            return
        result = False
        try:
            result = await self.user.log_out()
        except Exception:  # noqa: BLE001
            result = False
        file_removed = self.user.delete_session_file()
        with contextlib.suppress(Exception):
            await self.user.disconnect()
        if result and file_removed:
            await self._respond(event, "✅ Logged out.")
        elif result and not file_removed:
            await self._respond(event, "⚠️ Telegram पर logout हो गया, पर local session file delete नहीं हो सकी।")
        else:
            await self._respond(event, "⚠️ Logout पूरा नहीं हुआ। /login फिर से करें।")

    # -------------------------------------------------------------- helpers
    def _code_allowed(self) -> bool:
        now = time.time()
        self._code_times = [t for t in self._code_times if now - t < 3600]
        return len(self._code_times) < SEND_CODE_PER_HOUR

    def _note_possible_chat_block(self) -> None:
        self.log.warning("login code rejected right after entry; "
                         "Telegram may block codes sent through chat")

    async def _delete_incoming(self, event: Any) -> bool:
        try:
            await event.delete()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _consume_secret(self, event: Any) -> str:
        """Read a sensitive message, delete it at once, warn if that fails."""
        text = getattr(event, "text", None) or ""
        if not await self._delete_incoming(event):
            self.log.warning("could not delete a sensitive message")
            await self._respond(event, "⚠️ मैं आपका message delete नहीं कर सका। कृपया इसे मैन्युअल रूप से "
                                       "delete करें। / Please delete it manually.")
        return text

    async def _edit(self, event: Any, text: str, buttons: Any = None,
                    parse_mode: Any = None) -> None:
        """Edit the message a button belongs to; fall back to sending a new one."""
        edit = getattr(event, "edit", None)
        if edit is not None:
            try:
                await edit(text, buttons=buttons, parse_mode=parse_mode)
                return
            except Exception:  # noqa: BLE001 - fall back to a fresh message
                self.log.debug("could not edit the message; sending a new one")
        await self._respond(event, text, buttons=buttons, parse_mode=parse_mode)

    async def _respond(self, event: Any, text: str, buttons: Any = None,
                       parse_mode: Any = None) -> None:
        try:
            if buttons is not None:
                await event.respond(text, buttons=buttons, parse_mode=parse_mode)
            else:
                await event.respond(text, parse_mode=parse_mode)
        except Exception:  # noqa: BLE001
            self.log.warning("could not send a bot reply")
