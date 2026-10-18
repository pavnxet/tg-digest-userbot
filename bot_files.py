"""File commands (section F): /files /clean /find and the retention sweep."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

from telethon.tl.custom import Button

from files_store import FIND_LIMIT, clean_older_than, find, list_outputs
from timeutil import get_tz

_log = logging.getLogger("tgdigest.bot.filespanel")

_WINDOW_RE = re.compile(r"^(\d+)(h|d)$", re.IGNORECASE)


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size}{unit}"
        size //= 1024
    return f"{size}TB"


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
        app.commands.update({"/files": self.cmd_files, "/clean": self.cmd_clean, "/find": self.cmd_find})
        app.register_confirmation("clean", self._do_clean)

    def _arg(self, event: Any) -> str:
        return (getattr(event, "text", "") or "").partition(" ")[2].strip()

    # -- /files ------------------------------------------------------------
    async def cmd_files(self, event: Any) -> None:
        infos = list_outputs(self.output_dir)
        if not infos:
            await self.app._respond(event, "कोई output नहीं मिला।")
            return
        self._infos = infos[:20]
        lines = []
        for index, info in enumerate(self._infos, start=1):
            when = datetime.fromtimestamp(info.mtime, tz=get_tz()).strftime("%d/%m %H:%M")
            lines.append(f"{index}. {info.name} — {_human_size(info.size)} — {when} — {info.messages} msgs")
        buttons = []
        for index in range(1, len(self._infos) + 1):
            data = f"file:{index}".encode()
            self.app.register_callback(data, self._make_resend(index))
            buttons.append([Button.inline(f"📎 {index}", data)])
        await self.app._respond(event, "📁 Recent outputs:\n" + "\n".join(lines), buttons=buttons)

    def _make_resend(self, index: int):
        async def handler(event: Any) -> None:
            if not (1 <= index <= len(self._infos)):
                await self.app._respond(event, "यह file अब उपलब्ध नहीं।")
                return
            info = self._infos[index - 1]
            try:
                await self.app.bot.send_file(event.chat_id, str(info.path), caption=info.name)
            except Exception:  # noqa: BLE001
                await self.app._respond(event, "⚠️ भेजने में समस्या।")

        return handler

    # -- /clean ------------------------------------------------------------
    async def cmd_clean(self, event: Any) -> None:
        days = int(self.settings.get("retention_days"))
        await self.app.ask_confirmation(
            event, f"{days} दिन से पुरानी digest files हटाएँ?", "clean", {"days": days},
        )

    async def _do_clean(self, event: Any, payload: dict) -> None:
        deleted = clean_older_than(self.output_dir, int(payload["days"]))
        await self.app._respond(event, f"🗑 {len(deleted)} file हटाईं।")

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
            await self.app._respond(event, f"'{keyword}' कहीं नहीं मिला।")
            return
        lines = [f"🔎 {len(matches)} match(es) for '{keyword}':"]
        for match in matches:
            head = f"[{match['channel']}] {match['time'] or ''}".strip()
            lines.append(f"\n{head}\n{match['snippet']}")
            if match["link"]:
                lines.append(match["link"])
        await self.app._respond(event, "\n".join(lines))
