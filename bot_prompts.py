"""Prompt commands (section 4): /prompts and /prompt <name>.

Owner-only, private chat only, through the shared owner guard. Nothing here
contacts an AI service; it only reads files from prompts/.
"""
from __future__ import annotations

import io
import logging
from typing import Any

from prompts import PromptError, available_names, list_prompts, load_prompt

_log = logging.getLogger("tgdigest.bot.prompts")

# Telegram messages are capped at 4096 characters; stay comfortably under it.
MAX_MESSAGE_CHARS = 4000


class PromptPanel:
    """Adds /prompts and /prompt."""

    def __init__(self, app: Any, settings: Any) -> None:
        self.app = app
        self.settings = settings
        app.commands.update({"/prompts": self.cmd_prompts, "/prompt": self.cmd_prompt})

    def _arg(self, event: Any) -> str:
        return (getattr(event, "text", "") or "").partition(" ")[2].strip()

    async def cmd_prompts(self, event: Any) -> None:
        infos = list_prompts()
        current = self.settings.get("summary_prompt")
        if not infos:
            await self.app._respond(event, "कोई prompt नहीं मिला (prompts/ folder मौजूद है?)।")
            return
        lines = [
            f"{'★' if info.name == current else '·'} {info.name} — {info.description}"
            for info in infos
        ]
        await self.app._respond(event, f"📝 Prompts (selected: {current})\n" + "\n".join(lines))

    async def cmd_prompt(self, event: Any) -> None:
        name = self._arg(event)
        if not name:
            await self.app._respond(event, "उपयोग: /prompt <name>  — " + ", ".join(available_names()))
            return
        try:
            text = load_prompt(name)
        except PromptError as exc:
            await self.app._respond(event, f"❌ {exc}\nAvailable: " + ", ".join(available_names()))
            return
        if len(text) <= MAX_MESSAGE_CHARS:
            await self.app._respond(event, f"📝 {name}\n\n{text}")
        else:
            buffer = io.BytesIO(text.encode("utf-8"))
            buffer.name = f"{name}.md"
            await self.app.bot.send_file(event.chat_id, buffer, caption=f"{name}.md")
