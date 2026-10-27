"""Settings commands (section E): /settings and /set."""
from __future__ import annotations

from typing import Any

from settings_store import SETTING_HELP


class SettingsPanel:
    """Shows and changes the validated settings."""

    def __init__(self, app: Any, settings: Any) -> None:
        self.app = app
        self.settings = settings
        app.commands.update({"/settings": self.cmd_settings, "/set": self.cmd_set})

    def _arg(self, event: Any) -> str:
        return (getattr(event, "text", "") or "").partition(" ")[2].strip()

    async def cmd_settings(self, event: Any) -> None:
        """Every setting with what it does and the values it accepts."""
        lines = ["⚙️ Settings", "बदलने के लिए: /set <key> <value>", ""]
        for key in sorted(self.settings.all()):
            lines.append(f"• {key} = {self.settings.get(key)}")
            description, allowed = SETTING_HELP.get(key, ("", ""))
            if description:
                lines.append(f"   {description} — {allowed}")
        await self.app._respond(event, "\n".join(lines))

    async def cmd_set(self, event: Any) -> None:
        parts = self._arg(event).split(None, 1)
        if len(parts) < 2:
            await self.app._respond(
                event,
                "उपयोग: /set <key> <value>\nमान्य keys: " + ", ".join(sorted(self.settings.all())),
            )
            return
        ok, message = self.settings.set(parts[0], parts[1])
        await self.app._respond(event, ("✅ " if ok else "❌ ") + message)
