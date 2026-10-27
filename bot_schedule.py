"""Scheduled digests (section D): /schedule commands and the scheduler loop."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from telethon.tl.custom import Button

from schedule_store import MAX_SCHEDULES

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
            return (
                "कोई schedule नहीं है।\n\n"
                "जोड़ने के लिए: /schedule add 09:00 today\n"
                "(हर रोज़ सुबह 9 बजे आज का digest)"
            )
        now = datetime.now(self.store.tz)
        lines = [f"🗓 Schedules — {len(items)}/{MAX_SCHEDULES} (IST)", ""]
        for index, item in enumerate(items, start=1):
            scope = f"group:{item.tag}" if item.tag else "सभी channels"
            if item.paused:
                lines.append(f"{index}. ⏸ {item.time} — {item.window} · {scope}")
                lines.append(f"   रुका हुआ — /schedule resume {index} से चालू करें")
                continue
            lines.append(f"{index}. ⏰ {item.time} — {item.window} · {scope}")
            lines.append(f"   अगली बार: {self._describe(self._next_time(item, now), now)}")
            if item.last_fired:
                lines.append(f"   पिछली बार: {item.last_fired} ✅")
        lines += ["", "बदलें: /schedule remove|pause|resume <n | HH:MM>"]
        return "\n".join(lines)

    @staticmethod
    def _next_time(item: Any, now: datetime) -> datetime:
        hour, minute = (int(part) for part in item.time.split(":"))
        fire = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return fire if fire > now else fire + timedelta(days=1)

    @staticmethod
    def _describe(fire: datetime, now: datetime) -> str:
        total = int((fire - now).total_seconds() // 60)
        hours, minutes = divmod(total, 60)
        wait = f"{hours}h {minutes}m" if hours else f"{minutes}m"
        day = "आज" if fire.date() == now.date() else "कल"
        return f"{day} {fire:%H:%M} ({wait} बाद)"

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
                    tag = token.split(":", 1)[1] or None
            if tag:
                # A tag nobody uses would make the schedule silently do nothing.
                store = getattr(self.app, "channels_store", None)
                if store is not None:
                    known = sorted({t for entry in store.entries() for t in entry.tags})
                    if tag not in known:
                        await self.app._respond(
                            event,
                            f"❌ '{tag}' tag किसी channel पर नहीं है।\n"
                            f"मौजूदा tags: {', '.join(known) if known else 'कोई नहीं'}\n"
                            "पहले /tag <#> <tag> से tag लगाएँ।",
                        )
                        return
            ok, message = self.store.add(time, window, tag)
            await self.app._respond(event, ("✅ " if ok else "❌ ") + message)
        elif sub == "remove":
            index = self.store.index_of(rest)
            if index is None:
                await self.app._respond(
                    event, self._missing(rest) + "\n\n" + self._render(),
                )
                return
            item = self.store.all()[index - 1]
            # Bind the confirmation to the schedule itself, not to its number:
            # numbers shift as soon as one is removed.
            await self.app.ask_confirmation(
                event,
                f"Schedule #{index} ({item.time} — {item.window}) हटाएँ?",
                "schedule_remove",
                {"time": item.time},
            )
        elif sub in ("pause", "resume"):
            index = self.store.index_of(rest)
            if index is None:
                await self.app._respond(
                    event, self._missing(rest) + "\n\n" + self._render(),
                )
                return
            ok, message = self.store.set_paused(index, sub == "pause")
            await self.app._respond(event, ("✅ " if ok else "❌ ") + message)
        else:
            await self.app._respond(event, "उपयोग: /schedule add|list|remove|pause|resume")

    def _missing(self, rest: str) -> str:
        """A clear reason for an identifier that matches nothing."""
        text = (rest or "").strip().lstrip("#")
        if text.isdigit():
            return f"❌ #{text} नहीं है — अभी {len(self.store.all())} schedule हैं।"
        return f"❌ '{text}' समय का कोई schedule नहीं है।"

    async def _do_remove(self, event: Any, payload: dict) -> None:
        # Re-resolve by time: the list may have changed since the prompt.
        index = self.store.index_of(str(payload.get("time", "")))
        if index is None:
            await self.app._respond(
                event, "❌ वह schedule अब नहीं है।\n\n" + self._render(),
            )
            return
        ok, message = self.store.remove(index)
        if not ok:
            await self.app._respond(event, "❌ " + message)
            return
        # Show the new list, so the numbers you see are the numbers that exist.
        await self.app._respond(event, f"✅ {message}\n\n{self._render()}")


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
