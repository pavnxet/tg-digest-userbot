"""Scheduled digests (section D).

Times are IST. Stored in schedule.json (git-ignored), validated on load; a
corrupted file is ignored with a warning instead of crashing.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from run_args import WINDOW_TOKENS
from timeutil import get_tz

_log = logging.getLogger("tgdigest.bot.schedule")
MAX_SCHEDULES = 5
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass
class Schedule:
    time: str                       # "HH:MM" IST
    window: str                     # e.g. "today", "24h"
    tag: Optional[str] = None
    paused: bool = False
    last_fired: Optional[str] = None  # "YYYY-MM-DD" of the last fire


class ScheduleStore:
    def __init__(self, path: Path, tz=None) -> None:
        self.path = Path(path)
        self.tz = tz or get_tz()
        self._items: list[Schedule] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, list):
            _log.warning("schedule.json is not a list; ignoring")
            return
        for item in raw:
            try:
                self._items.append(Schedule(
                    time=str(item["time"]), window=str(item["window"]),
                    tag=item.get("tag"), paused=bool(item.get("paused", False)),
                    last_fired=item.get("last_fired"),
                ))
            except (KeyError, TypeError):
                _log.warning("skipping an invalid schedule entry")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([asdict(s) for s in self._items]), encoding="utf-8")

    # -- api ---------------------------------------------------------------
    def all(self) -> list[Schedule]:
        return list(self._items)

    def add(self, time: str, window: str, tag: Optional[str] = None) -> tuple[bool, str]:
        if not _TIME_RE.match(time or ""):
            return False, "समय HH:MM (IST) में दें, जैसे 09:00।"
        if not window:
            return False, "window चाहिए, जैसे today या 24h।"
        if window not in WINDOW_TOKENS:
            return False, "window गलत है। इनमें से एक: " + " · ".join(WINDOW_TOKENS)
        if any(item.time == time for item in self._items):
            # Two runs at the same minute collide with the 5-minute gap, so the
            # second would be refused anyway -- catch it here instead.
            return False, f"{time} पर पहले से एक schedule है। पहले उसे हटाएँ (/schedule list)।"
        if len(self._items) >= MAX_SCHEDULES:
            return False, f"अधिकतम {MAX_SCHEDULES} schedules अनुमत हैं।"
        self._items.append(Schedule(time=time, window=window, tag=tag))
        self._save()
        return True, "जोड़ दिया। / added."

    def index_of(self, identifier: str) -> Optional[int]:
        """Resolve `#2`, `2` or `09:10` to a 1-based index (None when absent).

        The time form is the stable one: numbers shift after a removal.
        """
        text = (identifier or "").strip().lstrip("#")
        if not text:
            return None
        if text.isdigit():
            index = int(text)
            return index if 1 <= index <= len(self._items) else None
        for index, item in enumerate(self._items, start=1):
            if item.time == text:
                return index
        return None

    def remove(self, index: int) -> tuple[bool, str]:
        if not (1 <= index <= len(self._items)):
            return False, f"#{index} नहीं है — अभी {len(self._items)} schedule हैं। /schedule list देखें।"
        item = self._items.pop(index - 1)
        self._save()
        return True, f"{item.time} — हटा दिया। / removed."

    def set_paused(self, index: int, paused: bool) -> tuple[bool, str]:
        if not (1 <= index <= len(self._items)):
            return False, f"#{index} नहीं है — अभी {len(self._items)} schedule हैं। /schedule list देखें।"
        item = self._items[index - 1]
        item.paused = paused
        self._save()
        return True, f"{item.time} — " + ("pause कर दिया ⏸" if paused else "फिर से चालू ▶️")

    def pause_all(self) -> int:
        for item in self._items:
            item.paused = True
        self._save()
        return len(self._items)

    def mark_fired(self, index: int, now: datetime) -> None:
        self._items[index - 1].last_fired = now.date().isoformat()
        self._save()

    # -- time --------------------------------------------------------------
    def _today_at(self, schedule: Schedule, now: datetime) -> datetime:
        hour, minute = (int(x) for x in schedule.time.split(":"))
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def due(self, now: Optional[datetime] = None) -> list[tuple[int, Schedule]]:
        now = now or datetime.now(self.tz)
        today = now.date().isoformat()
        out = []
        for index, item in enumerate(self._items, start=1):
            if item.paused or item.last_fired == today:
                continue
            fire = self._today_at(item, now)
            if fire.hour == now.hour and fire.minute == now.minute:
                out.append((index, item))
        return out

    def missed(self, now: Optional[datetime] = None) -> list[tuple[int, Schedule]]:
        """Non-paused schedules whose time today already passed without firing."""
        now = now or datetime.now(self.tz)
        today = now.date().isoformat()
        out = []
        for index, item in enumerate(self._items, start=1):
            if item.paused or item.last_fired == today:
                continue
            if self._today_at(item, now) < now:
                out.append((index, item))
        return out

    def next_run(self, now: Optional[datetime] = None) -> Optional[tuple[Schedule, datetime]]:
        now = now or datetime.now(self.tz)
        best: Optional[tuple[Schedule, datetime]] = None
        for item in self._items:
            if item.paused:
                continue
            fire = self._today_at(item, now)
            if fire <= now:
                fire = fire + timedelta(days=1)
            if best is None or fire < best[1]:
                best = (item, fire)
        return best
