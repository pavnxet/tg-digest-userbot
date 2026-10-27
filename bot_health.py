"""Health and kill switch (section G): /health and /panic."""
from __future__ import annotations

import contextlib
import logging
import shutil
import sys
import time
from datetime import datetime
from typing import Any

from files_store import list_outputs
from timeutil import get_tz

_log = logging.getLogger("tgdigest.bot.health")

KEEP_IN_CACHE = ("run_history.json", "run.lock")


class HealthPanel:
    """Adds /health and /panic."""

    def __init__(self, app: Any, run_manager: Any, output_dir: Any, cache_dir: Any,
                 schedule_store: Any = None, panic_delay: float = 3.0) -> None:
        self.app = app
        self.run_manager = run_manager
        self.output_dir = output_dir
        self.cache_dir = cache_dir
        self.schedule_store = schedule_store
        self.panic_delay = panic_delay
        self._started = time.time()
        self._panic_ready = 0.0
        app.commands.update({"/health": self.cmd_health, "/panic": self.cmd_panic})
        app.register_confirmation("panic1", self._panic_first)
        app.register_confirmation("panic2", self._panic_second)

    # -- /health -----------------------------------------------------------
    async def cmd_health(self, event: Any) -> None:
        lines = ["🩺 Health"]

        authorized = "unknown"
        try:
            await self.app.user.connect()
            authorized = "yes" if await self.app.user.is_authorized() else "no"
        except Exception as exc:  # noqa: BLE001 - locked session etc.
            authorized = f"error:{type(exc).__name__}"
        finally:
            with contextlib.suppress(Exception):
                await self.app.user.disconnect()
        lines.append(f"logged in: {authorized}")

        session = self.app.user.session_path
        lines.append(f"session file: {'present' if session.exists() else 'absent'}")

        last = self.run_manager.history.last()
        if last:
            when = datetime.fromtimestamp(float(last["started_at"]), tz=get_tz()).strftime("%d/%m %H:%M")
            lines.append(f"last run: {last['result']} at {when} ({int(last.get('duration', 0))}s)")
        else:
            lines.append("last run: none")

        outputs = list_outputs(self.output_dir)
        skipped: list[str] = []
        if outputs:
            import json

            try:
                meta = json.loads(outputs[0].path.read_text(encoding="utf-8")).get("meta", {})
                skipped = [f"{s['channel']}({s['reason']})" for s in (meta.get("skipped") or [])]
            except (OSError, ValueError):
                pass
        lines.append("skipped (last): " + (", ".join(skipped[:8]) if skipped else "none"))

        # Which channels keep failing, across the recent runs (not just the last).
        counts: dict[str, int] = {}
        reasons: dict[str, str] = {}
        import json as _json

        for info in outputs[:10]:
            try:
                meta = _json.loads(info.path.read_text(encoding="utf-8")).get("meta", {})
            except (OSError, ValueError):
                continue
            for item in meta.get("skipped") or []:
                name = str(item.get("channel") or "?")
                counts[name] = counts.get(name, 0) + 1
                reasons.setdefault(name, str(item.get("reason") or "?"))
        repeated = [(name, n) for name, n in counts.items() if n >= 2]
        if repeated:
            repeated.sort(key=lambda pair: -pair[1])
            lines.append(
                f"repeat failures (last {min(len(outputs), 10)} runs): "
                + ", ".join(f"{name}×{n}({reasons[name]})" for name, n in repeated[:5])
            )

        if self.schedule_store is not None:
            nxt = self.schedule_store.next_run()
            lines.append(f"next scheduled: {nxt[1].strftime('%d/%m %H:%M')} ({nxt[0].window})" if nxt else "next scheduled: none")

        try:
            free = shutil.disk_usage(self.output_dir).free
            lines.append(f"disk free (output): {free // (1024 * 1024)} MB")
        except OSError:
            lines.append("disk free: unknown")

        try:
            import telethon

            version = telethon.__version__
        except Exception:  # noqa: BLE001
            version = "?"
        lines.append(f"telethon {version}, python {sys.version_info.major}.{sys.version_info.minor}")
        lines.append(f"bot uptime: {int(time.time() - self._started)}s")
        await self.app._respond(event, "\n".join(lines))

    # -- /panic ------------------------------------------------------------
    async def cmd_panic(self, event: Any) -> None:
        await self.app.ask_confirmation(
            event, "⚠️ PANIC: schedules रोकूँगा, run बंद करूँगा, logout + session delete, "
                   "outputs और .cache मिटाऊँगा। पक्का?", "panic1", confirm_label="पक्का / Yes",
        )

    async def _panic_first(self, event: Any, payload: dict) -> None:
        self._panic_ready = time.time() + self.panic_delay
        await self.app.ask_confirmation(
            event, "आख़िरी बार: यह session और files मिटा देगा।", "panic2", confirm_label="अब करो / Do it",
        )

    async def _panic_second(self, event: Any, payload: dict) -> None:
        if time.time() < self._panic_ready:
            await self.app._respond(event, "⏳ थोड़ी देर रुकें, फिर दबाएँ।")
            return
        await self._run_panic(event)

    async def _run_panic(self, event: Any) -> None:
        report = []
        if self.schedule_store is not None:
            try:
                report.append(f"schedules paused: {self.schedule_store.pause_all()}")
            except Exception as exc:  # noqa: BLE001
                report.append(f"schedules pause failed: {type(exc).__name__}")
        try:
            report.append(f"run stopped: {await self.run_manager.stop()}")
        except Exception as exc:  # noqa: BLE001
            report.append(f"run stop failed: {type(exc).__name__}")
        try:
            await self.app._do_logout(event)
            report.append("logout: done")
        except Exception as exc:  # noqa: BLE001
            report.append(f"logout failed: {type(exc).__name__}")

        deleted = 0
        for info in list_outputs(self.output_dir):
            try:
                info.path.unlink()
                deleted += 1
            except OSError:
                pass
        report.append(f"outputs deleted: {deleted}")

        removed = kept = 0
        try:
            children = list(self.cache_dir.iterdir())
        except OSError:
            children = []
        for child in children:
            if child.name in KEEP_IN_CACHE:
                kept += 1
                continue
            try:
                shutil.rmtree(child) if child.is_dir() else child.unlink()
                removed += 1
            except OSError:
                pass
        report.append(f"cache removed: {removed} (kept {kept})")
        await self.app._respond(event, "🛑 Panic done:\n" + "\n".join(report))
