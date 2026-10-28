"""Run commands (section C): /run /stop /preview /last.

The bot is the control plane: it spawns `main.py` through the RunManager and
reports progress by editing one status message (at most once every 10 seconds).
`start_run` is reused by the scheduler (section D).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import secrets
import time
from pathlib import Path
from typing import Any, Optional

from telethon.tl.custom import Button

from config import HISTORY_TIMEOUT_SECONDS
from run_args import ArgError, build_argv

_log = logging.getLogger("tgdigest.bot.runpanel")

PROGRESS_INTERVAL = 10
MAX_AUTO_SEND_PARTS = 10
RESUME_TTL_SECONDS = 3600.0
_COUNT_RE = re.compile(r"OK\s+(.+?)\s+\(~(\d+)\s+message")


def _channels_file_arg(argv: list[str]) -> Optional[str]:
    """The --channels-file value in an argv list, if it has one."""
    if "--channels-file" in argv:
        index = argv.index("--channels-file")
        if index + 1 < len(argv):
            return argv[index + 1]
    return None


class RunPanel:
    """Adds /run /stop /preview /last to the bot."""

    def __init__(self, app: Any, run_manager: Any, settings: Any, output_dir: Path, cache_dir: Path) -> None:
        self.app = app
        self.rm = run_manager
        self.settings = settings
        self.output_dir = Path(output_dir)
        self.cache_dir = Path(cache_dir)
        self._task: Optional[asyncio.Task] = None
        self._status_id: Optional[int] = None
        self._last_outputs: list[Path] = []
        self._autodelete_task: Optional[asyncio.Task] = None
        self._last_all_history = False
        self._run_channels_file: Optional[str] = None
        self._last_argv: Optional[list[str]] = None
        self._last_send_files = True
        self._resume: dict[str, tuple[list[str], bool, float]] = {}
        app.register_callback_prefix(b"resume:", self._cb_resume)
        app.commands.update({
            "/run": self.cmd_run,
            "/history": self.cmd_history,
            "/stop": self.cmd_stop,
            "/preview": self.cmd_preview,
            "/last": self.cmd_last,
        })

    def _arg(self, event: Any) -> str:
        return (getattr(event, "text", "") or "").partition(" ")[2].strip()

    # -- subset writer for group:/channel: filters -------------------------
    def _subset(self, token: str) -> tuple[Optional[str], Optional[str]]:
        store = getattr(self.app, "channels_store", None)
        if store is None:
            return None, "channels store उपलब्ध नहीं।"
        kind, _, value = token.partition(":")
        entries = store.entries()
        chosen = [e for e in entries if value in e.tags] if kind == "group" else \
            [e for e in entries if str(e.index) == value]
        if not chosen:
            return None, f"{token}: कुछ नहीं मिला।"
        path = self.cache_dir / "run_channels.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(e.raw for e in chosen) + "\n", encoding="utf-8")
        return str(path), None

    def _total_channels(self) -> int:
        # Prefer the subset this run actually uses. Without this the progress line
        # reports the size of the whole channels.txt (e.g. "channel 0/4") even
        # when the run is a single channel or one group.
        path = self._run_channels_file
        if path:
            try:
                from channels import parse_channels_file

                active = [s for s in parse_channels_file(Path(path)) if not s.paused]
                if active:
                    return len(active)
            except OSError:
                pass
        store = getattr(self.app, "channels_store", None)
        return 0 if store is None else len([e for e in store.entries() if not e.paused])

    def _channels_done(self) -> int:
        try:
            run_id = (self.cache_dir / "last_run_id.txt").read_text(encoding="utf-8").strip()
            checkpoint = self.cache_dir / f"{run_id}.jsonl"
            return sum(1 for line in checkpoint.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            return 0

    # -- shared entry point (used by /run, /history and the scheduler) -----
    async def start_run(self, text: str, chat_id: Any) -> Optional[str]:
        """Start a run. Returns an error message, or None on success."""
        try:
            argv, send_files = build_argv(text, self.settings, subset_writer=self._subset)
        except ArgError as exc:
            return f"❌ {exc}"
        return await self._start(argv, send_files, chat_id)

    async def _start(self, argv: list[str], send_files: bool, chat_id: Any,
                     timeout: Optional[int] = None) -> Optional[str]:
        """Spawn main.py with a validated argv. Returns an error message or None."""
        if timeout is None and "--all-history" in argv:
            timeout = HISTORY_TIMEOUT_SECONDS     # a full history needs far longer
        self._run_channels_file = _channels_file_arg(argv)
        refusal = await self.rm.start(argv, timeout=timeout)
        if refusal is not None:
            return f"❌ {refusal.reason}"
        self._last_argv = list(argv)
        self._last_send_files = send_files
        self._last_all_history = "--all-history" in argv
        self._task = asyncio.create_task(self._supervise(chat_id, send_files))
        return None

    # -- full history ------------------------------------------------------
    def _history_subset(self, target: str) -> tuple[Optional[str], Optional[str]]:
        """Write a one-line channels file for a single channel (#, @name or link)."""
        store = getattr(self.app, "channels_store", None)
        if store is None:
            return None, "channels store उपलब्ध नहीं।"
        matches = store.find(target)
        if not matches:
            return None, f"{target}: कोई चैनल नहीं मिला। /channels से नंबर देखें।"
        if len(matches) > 1:
            numbers = ", ".join(str(e.index) for e in matches[:8])
            return None, f"{target} कई चैनलों से मिलता है ({numbers})। नंबर दें: /history <#>"
        if matches[0].paused:
            return None, (
                f"#{matches[0].index} paused है — पहले /resume {matches[0].index} भेजें, "
                "फिर /history चलाएँ।"
            )
        path = self.cache_dir / "history_channel.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(matches[0].raw + "\n", encoding="utf-8")
        return str(path), None

    async def cmd_history(self, event: Any) -> None:
        if self.rm.running:
            await self.app._respond(event, "एक run पहले से चल रहा है। /stop से रोकें।")
            return
        text = self._arg(event)
        if not text:
            await self.app._respond(
                event,
                "उपयोग: /history <#|@name|link> [dedupe] [min:<N>] [views] [send:yes]\n"
                "एक चैनल की पूरी history scrape करता है (फ़ाइल तभी भेजी जाती है जब send:yes दें)।",
            )
            return
        target, _, extra = text.partition(" ")
        extra = extra.strip()
        for token in extra.split():
            if token.lower().startswith(("group:", "channel:")):
                await self.app._respond(
                    event, "❌ /history में group:/channel: की ज़रूरत नहीं — चैनल सीधे दें।",
                )
                return
        path, error = self._history_subset(target)
        if error or path is None:
            await self.app._respond(event, f"❌ {error or 'चैनल नहीं मिला।'}")
            return
        try:
            argv, send_files = build_argv(f"all {extra}".strip(), self.settings)
        except ArgError as exc:
            await self.app._respond(event, f"❌ {exc}")
            return
        argv += ["--channels-file", path]
        await self.app._respond(
            event,
            "📜 पूरी history scrape हो रही है — इसमें लंबा समय लग सकता है। /stop से रोक सकते हैं।",
        )
        error = await self._start(argv, send_files, event.chat_id)
        if error:
            await self.app._respond(event, error)

    # -- commands ----------------------------------------------------------
    async def cmd_run(self, event: Any) -> None:
        if self.rm.running:
            await self.app._respond(event, "एक run पहले से चल रहा है। /stop से रोकें।")
            return
        text = self._arg(event)
        if not text:
            await self.app._respond(event, "उपयोग: /run <window> [group:<tag>] [channel:<n>] [dedupe] [min:<N>] [views] [send:no] [resume]\n"
                                           "window: today | yesterday | since-last | all | कोई अवधि (6h, 24h, 5d, 1w) "
                                           "| <YYYY-MM-DD HH:MM> x2")
            return
        error = await self.start_run(text, event.chat_id)
        if error:
            await self.app._respond(event, error)

    async def _supervise(self, chat_id: Any, send_files: bool) -> None:
        status = await self.app.bot.send_message(chat_id, "▶️ Run शुरू…")
        self._status_id = getattr(status, "id", None)
        started = time.time()
        wait_task = asyncio.create_task(self.rm.wait())
        while not wait_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(wait_task), timeout=PROGRESS_INTERVAL)
            except asyncio.TimeoutError:
                await self._edit_status(chat_id, started)
        result = await wait_task
        await self._final(chat_id, result, send_files)

    async def _edit_status(self, chat_id: Any, started: float) -> None:
        if self._status_id is None:
            return
        elapsed = int(time.time() - started)
        done, total = self._channels_done(), self._total_channels()
        text = f"⏳ {elapsed}s — channel {done}/{total}" if total else f"⏳ {elapsed}s"
        with contextlib.suppress(Exception):  # editing the status is best-effort
            await self.app.bot.edit_message(chat_id, self._status_id, text)

    async def _final(self, chat_id: Any, result: Any, send_files: bool) -> None:
        self._last_outputs = self._new_outputs(result.started_at)
        buttons = self._resume_buttons(result)
        await self.app.bot.send_message(chat_id, self._summary(result, send_files), buttons=buttons)
        if send_files and self._last_outputs:
            await self._send_outputs(chat_id)

    def _resume_buttons(self, result: Any) -> Optional[list]:
        """Offer a Resume button when a run did not finish (its checkpoint is kept)."""
        if result.result not in ("timeout", "failed", "stopped") or not self._last_argv:
            return None
        token = secrets.token_urlsafe(6)
        argv = self._snapshot_channels_file(list(self._last_argv), token)
        self._resume[token] = (argv, self._last_send_files, time.time() + RESUME_TTL_SECONDS)
        while len(self._resume) > 10:
            self._drop_resume(next(iter(self._resume)))
        return [[Button.inline("🔁 Resume", f"resume:{token}".encode())]]

    def _snapshot_channels_file(self, argv: list[str], token: str) -> list[str]:
        """Give this Resume its own copy of the channels file.

        `history_channel.txt` / `run_channels.txt` are shared and overwritten by
        the next run, so a button that only stored the path would scrape whatever
        channel came next.
        """
        if "--channels-file" not in argv:
            return argv
        index = argv.index("--channels-file") + 1
        if index >= len(argv):
            return argv
        try:
            source = Path(argv[index])
            target = self.cache_dir / f"resume_{token}.txt"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            return argv
        argv[index] = str(target)
        return argv

    def _drop_resume(self, token: str) -> None:
        item = self._resume.pop(token, None)
        if item is None:
            return
        for argument in item[0]:
            if isinstance(argument, str) and argument.startswith(str(self.cache_dir / "resume_")):
                with contextlib.suppress(OSError):
                    Path(argument).unlink()

    async def _cb_resume(self, event: Any, rest: bytes) -> None:
        """Continue the run this button was made for, from its checkpoint."""
        token = rest.decode("utf-8", "replace")
        item = self._resume.pop(token, None)
        if item is None or time.time() > item[2]:
            await self.app._respond(event, "यह button पुराना है। /run फिर से भेजें।")
            return
        argv, send_files, _expiry = item
        if self.rm.running:
            await self.app._respond(event, "एक run पहले से चल रहा है। /stop से रोकें।")
            return
        error = await self._start([*argv, "--resume"], send_files, event.chat_id)
        if error:
            await self.app._respond(event, error)

    def _new_outputs(self, since: float) -> list[Path]:
        files = sorted(self.output_dir.glob("digest_*"), key=lambda p: p.stat().st_mtime)
        return [p for p in files if p.stat().st_mtime >= since - 2]

    def _summary(self, result: Any, send_files: bool = True) -> str:
        icon = {"ok": "✅", "failed": "❌", "timeout": "⏱", "stopped": "🛑"}.get(result.result, "•")
        lines = [f"{icon} Run {result.result} — {int(result.duration)}s"]
        if self._last_outputs:
            try:
                meta = json.loads(self._last_outputs[0].read_text(encoding="utf-8")).get("meta", {})
                lines.append(f"channels_ok={meta.get('channels_ok', 0)} messages={meta.get('messages_total', 0)}")
                skipped = meta.get("skipped") or []
                if skipped:
                    lines.append("skipped: " + ", ".join(f"{s['channel']}({s['reason']})" for s in skipped[:8]))
                if meta.get("truncated_channels"):
                    lines.append("truncated: " + ", ".join(meta["truncated_channels"]))
                total_chars = sum(p.stat().st_size for p in self._last_outputs)
                lines.append(f"~tokens≈{total_chars // 4} (Latin) / {int(total_chars / 1.5)} (Devanagari)")
            except (OSError, ValueError):
                pass
            lines.append(f"files: {len(self._last_outputs)} — {self.output_dir.name}/ folder में")
            if not send_files:
                lines.append("फ़ाइल भेजी नहीं गई — /last भेजने से मिल जाएगी।")
        if self._last_all_history and result.result in ("timeout", "failed", "stopped"):
            lines.append(
                "यह लंबा run पूरा नहीं हुआ। जहाँ तक पढ़ा गया वह checkpoint में सुरक्षित है — "
                "जारी रखने के लिए वही command दोबारा भेजें और अंत में `resume` जोड़ें "
                "(जैसे /history <#> resume)।"
            )
        return "\n".join(lines)

    async def _send_outputs(self, chat_id: Any) -> None:
        parts = self._last_outputs
        if len(parts) > MAX_AUTO_SEND_PARTS:
            await self.app.bot.send_message(chat_id, f"{len(parts)} parts हैं। भेजने के लिए /last भेजें।")
            return
        total = len(parts)
        sent_ids: list[int] = []
        for index, path in enumerate(parts, start=1):
            caption = f"part {index} of {total}" if total > 1 else path.name
            try:
                message = await self.app.bot.send_file(chat_id, str(path), caption=caption)
            except Exception:  # noqa: BLE001
                await self.app.bot.send_message(chat_id, "⚠️ file भेजने में समस्या। /last से फिर भेजें।")
                return
            message_id = getattr(message, "id", None)
            if message_id is not None:
                sent_ids.append(message_id)
        await self._maybe_autodelete(chat_id, sent_ids)

    async def _maybe_autodelete(self, chat_id: Any, message_ids: list[int]) -> None:
        """Delete the digests we just sent, after `autodelete_minutes`."""
        minutes = int(self.settings.get("autodelete_minutes") or 0)
        if minutes <= 0 or not message_ids:
            return

        async def _later() -> None:
            await asyncio.sleep(minutes * 60)
            try:
                await self.app.bot.delete_messages(chat_id, list(message_ids))
                _log.info("autodelete: removed %s digest message(s)", len(message_ids))
            except Exception:  # noqa: BLE001 - best effort, never crash the bot
                _log.warning("autodelete failed; the digest messages stay in the chat")

        self._autodelete_task = asyncio.create_task(_later())

    async def cmd_stop(self, event: Any) -> None:
        stopped = await self.rm.stop()
        await self.app._respond(event, "🛑 रोक दिया।" if stopped else "कोई run नहीं चल रहा।")

    async def cmd_last(self, event: Any) -> None:
        outputs = sorted(self.output_dir.glob("digest_*"), key=lambda p: p.stat().st_mtime)
        if not outputs:
            await self.app._respond(event, "कोई digest नहीं मिला।")
            return
        base = outputs[-1].name.split("_part")[0].split(".json")[0]
        self._last_outputs = [p for p in outputs if p.name.startswith(base)]
        await self._send_outputs(event.chat_id)

    async def cmd_preview(self, event: Any) -> None:
        if self.rm.running:
            await self.app._respond(event, "एक run पहले से चल रहा है।")
            return
        text = self._arg(event)
        if not text:
            await self.app._respond(event, "उपयोग: /preview <window> [group:<tag>]")
            return
        try:
            argv, _ = build_argv(text, self.settings, subset_writer=self._subset)
        except ArgError as exc:
            await self.app._respond(event, f"❌ {exc}")
            return
        refusal = await self.rm.start([*argv, "--dry-run"], capture=True, dry_run=True)
        if refusal is not None:
            await self.app._respond(event, f"❌ {refusal.reason}")
            return
        result = await self.rm.wait()
        counts = _COUNT_RE.findall(result.output)
        if not counts:
            await self.app._respond(event, "पूर्वावलोकन: कोई message नहीं मिला।")
            return
        lines = [f"{name}: ~{n}" for name, n in counts[:30]]
        await self.app._respond(event, "🔎 Preview:\n" + "\n".join(lines))
