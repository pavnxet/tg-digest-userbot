"""File commands (section F): /files /clean /find and the retention sweep."""
from __future__ import annotations

import logging
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from telethon.tl.custom import Button

from files_store import (
    FIND_LIMIT,
    _safe_in_dir,
    clean_older_than,
    delete_files,
    files_to_clean,
    find,
    list_outputs,
)
from timeutil import get_tz

_log = logging.getLogger("tgdigest.bot.filespanel")

FILE_TOKEN_TTL = 3600.0   # an hour to press a resend button

_WINDOW_RE = re.compile(r"^(\d+)(h|d)$", re.IGNORECASE)


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size}{unit}"
        size //= 1024
    return f"{size}TB"


def _days_arg(token: str) -> Optional[int]:
    """'3d' or '3' -> 3 days. None when it is not understood."""
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    match = re.fullmatch(r"(\d+)\s*d", token)
    return int(match.group(1)) if match else None


def _window_hours(token: str) -> Optional[float]:
    token = token.lower()
    if token == "today":  # nosec B105 - window keyword
        return 24.0
    if token == "yesterday":  # nosec B105 - window keyword
        return 48.0
    match = _WINDOW_RE.match(token)
    if not match:
        return None
    amount = int(match.group(1))
    return amount * 24 if match.group(2).lower() == "d" else float(amount)


class FilePanel:
    """Adds /files /clean /find."""

    def __init__(self, app: Any, output_dir: Any, settings: Any) -> None:
        self.app = app
        self.output_dir = output_dir
        self.settings = settings
        self._infos: list = []
        self._files: dict[str, tuple[str, float]] = {}   # token -> (path, expiry)
        app.commands.update({"/files": self.cmd_files, "/clean": self.cmd_clean, "/find": self.cmd_find})
        app.register_confirmation("clean", self._do_clean)
        app.register_callback_prefix(b"file:", self._cb_file)
        app.register_callback_prefix(b"del:", self._cb_del)
        app.register_confirmation("delete_file", self._do_delete_file)

    def _arg(self, event: Any) -> str:
        return (getattr(event, "text", "") or "").partition(" ")[2].strip()

    # -- /files ------------------------------------------------------------
    async def cmd_files(self, event: Any) -> None:
        infos = list_outputs(self.output_dir)
        if not infos:
            await self.app._respond(
                event, "कोई output नहीं मिला। पहला digest बनाने के लिए /run today भेजें।",
            )
            return
        self._infos = infos[:20]
        total = sum(info.size for info in infos)
        lines = []
        for index, info in enumerate(self._infos, start=1):
            when = datetime.fromtimestamp(info.mtime, tz=get_tz()).strftime("%d/%m %H:%M")
            lines.append(f"{index}. {info.name} — {_human_size(info.size)} — {when} — {info.messages} msgs")
        # Each button carries a token bound to *that* file, so pressing a button
        # from an older /files list can never send a different file.
        buttons = []
        now = time.time()
        self._files = {k: v for k, v in self._files.items() if v[1] > now}   # prune expired
        while len(self._files) > 100:
            self._files.pop(next(iter(self._files)))
        for index, info in enumerate(self._infos, start=1):
            token = secrets.token_urlsafe(6)
            self._files[token] = (str(info.path), now + FILE_TOKEN_TTL)
            buttons.append([
                Button.inline(f"📎 {index}", f"file:{token}".encode()),
                Button.inline(f"🗑 {index}", f"del:{token}".encode()),
            ])
        await self.app._respond(event, f"📁 Recent outputs — {len(infos)} file(s), {_human_size(total)}:\n" + "\n".join(lines), buttons=buttons)

    async def _cb_file(self, event: Any, rest: bytes) -> None:
        """Resend the file this button was created for."""
        token = rest.decode("utf-8", "replace")
        item = self._files.get(token)      # not popped: the delete button shares it
        if item is None or time.time() > item[1]:
            await self.app._respond(event, "यह button पुराना है। /files फिर से भेजें।")
            return
        path = Path(item[0])
        if not path.exists() or not _safe_in_dir(path, self.output_dir):
            await self.app._respond(event, "वह file अब मौजूद नहीं है। /files देखें।")
            return
        try:
            await self.app.bot.send_file(event.chat_id, str(path), caption=path.name)
        except Exception:  # noqa: BLE001
            await self.app._respond(event, "⚠️ भेजने में समस्या। /last से फिर भेजें।")

    async def _cb_del(self, event: Any, rest: bytes) -> None:
        """Delete the file this button was made for (after a confirmation)."""
        token = rest.decode("utf-8", "replace")
        item = self._files.get(token)
        if item is None or time.time() > item[1]:
            await self.app._respond(event, "यह button पुराना है। /files फिर से भेजें।")
            return
        path = Path(item[0])
        if not path.exists() or not _safe_in_dir(path, self.output_dir):
            await self.app._respond(event, "वह file अब मौजूद नहीं है।")
            return
        await self.app.ask_confirmation(
            event, f"{path.name} हटाएँ? / Delete this file?", "delete_file", {"path": str(path)},
        )

    async def _do_delete_file(self, event: Any, payload: dict) -> None:
        path = Path(payload.get("path", ""))
        if not _safe_in_dir(path, self.output_dir):
            await self.app._respond(event, "❌ यह file output folder में नहीं है।")
            return
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            await self.app._respond(event, "❌ हटाया नहीं जा सका।")
            return
        await self.app._respond(event, f"🗑 {path.name} हटाई — {_human_size(size)} खाली हुआ।")

    # -- /clean ------------------------------------------------------------
    async def cmd_clean(self, event: Any) -> None:
        """Preview what will go, then confirm. `/clean all` clears everything."""
        arg = self._arg(event).strip().lower()
        days: Optional[int] = int(self.settings.get("retention_days"))
        if arg == "all":
            days = None
        elif arg:
            parsed = _days_arg(arg)
            if parsed is None or not (1 <= parsed <= 3650):
                # 0 days would mean \"everything\", which /clean all says explicitly.
                await self.app._respond(
                    event,
                    "उपयोग:\n"
                    f"/clean — {int(self.settings.get('retention_days'))} दिन से पुरानी files\n"
                    "/clean 3d या /clean 3 — 3 दिन से पुरानी (1 से 3650)\n"
                    "/clean all — सारी digest files (तुरंत)",
                )
                return
            days = parsed

        targets = files_to_clean(self.output_dir, days)
        scope = "सभी" if days is None else f"{days} दिन से पुरानी"
        if not targets:
            await self.app._respond(
                event, f"🧹 {scope} कोई file नहीं मिली — हटाने के लिए कुछ नहीं।",
            )
            return

        total = sum(info.size for info in targets)
        lines = [f"🧹 {scope} {len(targets)} file(s) — कुल {_human_size(total)}", ""]
        for info in targets[:10]:
            when = datetime.fromtimestamp(info.mtime, tz=get_tz()).strftime("%d/%m %H:%M")
            lines.append(f"• {info.name} — {_human_size(info.size)} — {when}")
        if len(targets) > 10:
            lines.append(f"… और {len(targets) - 10} file(s)")
        lines += ["", "हटाएँ? यह वापस नहीं आएँगी।"]
        await self.app.ask_confirmation(event, "\n".join(lines), "clean", {"days": days})

    async def _do_clean(self, event: Any, payload: dict) -> None:
        targets = files_to_clean(self.output_dir, payload.get("days"))
        names, freed = delete_files(targets)
        if not names:
            await self.app._respond(event, "🗑 कुछ भी हटाया नहीं गया।")
            return
        await self.app._respond(
            event,
            f"🗑 {len(names)} file(s) हटाईं — {_human_size(freed)} खाली हुआ।\n"
            "बाक़ी files देखने के लिए /files।",
        )

    def run_retention(self) -> list[str]:
        """Called at startup and once a day. Never touches .cache/."""
        days = int(self.settings.get("retention_days"))
        return clean_older_than(self.output_dir, days)

    # -- /find -------------------------------------------------------------
    async def cmd_find(self, event: Any) -> None:
        parts = self._arg(event).split()
        if not parts:
            await self.app._respond(event, "उपयोग: /find <keyword> [6h|24h|3d]")
            return
        keyword = parts[0]
        hours = None
        if len(parts) > 1:
            hours = _window_hours(parts[1])
            if hours is None:
                await self.app._respond(event, "window: 6h | 24h | 3d | today")
                return
        matches = find(keyword, self.output_dir, hours, FIND_LIMIT)
        if not matches:
            await self.app._respond(
                event,
                f"'{keyword}' कहीं नहीं मिला।\n"
                "बड़ा window आज़माएँ (/find <keyword> 3d) या /files से देखें कि कौन-सी files हैं।",
            )
            return
        lines = [f"🔎 {len(matches)} match(es) for '{keyword}':"]
        for match in matches:
            head = f"[{match['channel']}] {match['time'] or ''}".strip()
            lines.append(f"\n{head}\n{match['snippet']}")
            if match["link"]:
                lines.append(match["link"])
        await self.app._respond(event, "\n".join(lines))
