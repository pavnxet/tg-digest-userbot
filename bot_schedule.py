"""Scheduled digests (section D): /schedule commands and the scheduler loop."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Optional

from telethon.tl.custom import Button

_log = logging.getLogger("tgdigest.bot.scheduler")

CHECK_INTERVAL = 30


class SchedulePanel:
    """Adds /schedule add|list|remove|pause|resume."""

    def __init__(self, app: Any, store: Any) -> None:
        self.app = app
        self.store = store
        app.commands["/schedule"] = self.cmd_schedule
        app.register_confirmation("schedule_remove", self._do_remove)

    def _arg(self, event: Any) -> str:
        return (getattr(event, "text", "") or "").partition(" ")[2].strip()

    def _render(self) -> str:
        items = self.store.all()
        if not items:
            return "कोई schedule नहीं। /schedule add 09:00 today"
        lines = []
        for index, item in enumerate(items, start=1):
            mark = "⏸ " if item.paused else ""
            tag = f" group:{item.tag}" if item.tag else ""
            lines.append(f"{index}. {mark}{item.time} — {item.window}{tag}")
        return "🗓 Schedules:\n" + "\n".join(lines)

    async def cmd_schedule(self, event: Any) -> None:
        parts = self._arg(event).split(None, 1)
        sub = parts[0].lower() if parts else "list"
        rest = parts[1] if len(parts) > 1 else ""

        if sub in ("list", ""):
            await self.app._respond(event, self._render())
        elif sub == "add":
            tokens = rest.split()
            if len(tokens) < 2:
                await self.app._respond(event, "उपयोग: /schedule add <HH:MM> <window> [group:<tag>]")
                return
            time, window = tokens[0], tokens[1]
            tag = None
            for token in tokens[2:]:
                if token.lower().startswith("group:"):
                    tag = token.split(":", 1)[1]
            ok, message = self.store.add(time, window, tag)
            await self.app._respond(event, ("✅ " if ok else "❌ ") + message)
        elif sub == "remove":
            index = self._index(rest)
            if index is None:
                await self.app._respond(event, "उपयोग: /schedule remove <n>")
                return
            await self.app.ask_confirmation(event, f"Schedule #{index} हटाएँ?", "schedule_remove", {"index": index})
        elif sub in ("pause", "resume"):
            index = self._index(rest)
            if index is None:
                await self.app._respond(event, f"उपयोग: /schedule {sub} <n>")
                return
            ok, message = self.store.set_paused(index, sub == "pause")
            await self.app._respond(event, ("✅ " if ok else "❌ ") + message)
        else:
            await self.app._respond(event, "उपयोग: /schedule add|list|remove|pause|resume")

    def _index(self, text: str) -> Optional[int]:
        text = text.strip().lstrip("#")
        return int(text) if text.isdigit() else None

    async def _do_remove(self, event: Any, payload: dict) -> None:
        ok, message = self.store.remove(int(payload["index"]))
        await self.app._respond(event, ("✅ " if ok else "❌ ") + message)


class Scheduler:
    """Fires scheduled runs. Missed runs are not caught up automatically."""

    def __init__(self, app: Any, store: Any, run_panel: Any, interval: int = CHECK_INTERVAL) -> None:
        self.app = app
        self.store = store
        self.run_panel = run_panel
        self.interval = interval

    def due(self, now: Optional[datetime] = None):
        return self.store.due(now)

    async def _fire(self, index: int, schedule: Any) -> None:
        chat = self.app.owners.owner_id
        now = datetime.now(self.store.tz)
        if chat is None:
            return
        if self.run_panel.rm.running:
            await self.app.bot.send_message(chat, f"⏭ {schedule.time} skip: एक run पहले से चल रहा है।")
        else:
            text = schedule.window + (f" group:{schedule.tag}" if schedule.tag else "")
            error = await self.run_panel.start_run(text, chat)
            if error:
                await self.app.bot.send_message(chat, f"❌ scheduled run: {error}")
        self.store.mark_fired(index, now)

    async def run_forever(self) -> None:
        while True:
            try:
                for index, schedule in self.store.due():
                    await self._fire(index, schedule)
            except Exception:  # noqa: BLE001 - the loop must never die
                _log.exception("scheduler tick failed")
            await asyncio.sleep(self.interval)

    async def startup_check(self, now: Optional[datetime] = None) -> list:
        """Tell the owner about runs missed while the bot was down."""
        chat = self.app.owners.owner_id
        if chat is None:
            return []
        missed = self.store.missed(now)
        for index, schedule in missed:
            data = f"sched:run:{index}".encode()
            self.app.register_callback(data, self._make_run_now(schedule))
            await self.app.bot.send_message(
                chat,
                f"⏰ {schedule.time} ({schedule.window}) छूट गया — bot बंद था। अभी चलाएँ?",
                buttons=[[Button.inline("Run now", data)]],
            )
        return missed

    def _make_run_now(self, schedule: Any):
        async def handler(event: Any) -> None:
            text = schedule.window + (f" group:{schedule.tag}" if schedule.tag else "")
            error = await self.run_panel.start_run(text, event.chat_id)
            if error:
                await self.app._respond(event, error)

        return handler
