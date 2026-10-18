"""Settings commands (section E): /settings and /set."""
from __future__ import annotations

from typing import Any


class SettingsPanel:
    """Shows and changes the validated settings."""

    def __init__(self, app: Any, settings: Any) -> None:
        self.app = app
        self.settings = settings
        app.commands.update({"/settings": self.cmd_settings, "/set": self.cmd_set})

    def _arg(self, event: Any) -> str:
        return (getattr(event, "text", "") or "").partition(" ")[2].strip()

    async def cmd_settings(self, event: Any) -> None:
        lines = [f"{key} = {value}" for key, value in sorted(self.settings.all().items())]
        await self.app._respond(event, "⚙️ Settings:\n" + "\n".join(lines))

    async def cmd_set(self, event: Any) -> None:
        parts = self._arg(event).split(None, 1)
        if len(parts) < 2:
            await self.app._respond(event, "उपयोग: /set <key> <value>")
            return
        ok, message = self.settings.set(parts[0], parts[1])
        await self.app._respond(event, ("✅ " if ok else "❌ ") + message)
