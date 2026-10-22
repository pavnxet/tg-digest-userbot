"""Control-plane run manager (section A).

The bot never scrapes itself. It launches ``main.py`` as a *separate process*,
with an argv **list** and ``shell=False``, so chat text can never be interpreted
by a shell. Only one run happens at a time (crash-safe lock file), runs are
rate-limited (5-minute gap, 12 per IST day), and a run has a hard timeout whose
process tree is killed cleanly.

No secret, message content or channel list is ever logged here -- only counts,
durations and result words.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from timeutil import get_tz

_log = logging.getLogger("tgdigest.bot.run")

RESULT_OK = "ok"
RESULT_FAILED = "failed"
RESULT_TIMEOUT = "timeout"
RESULT_STOPPED = "stopped"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class RunResult:
    started_at: float
    finished_at: float
    exit_code: Optional[int]
    result: str
    duration: float
    output: str = ""

    @property
    def ok(self) -> bool:
        return self.result == RESULT_OK


@dataclass
class Refusal:
    """A run was not started. `reason` is shown to the owner."""

    reason: str


class RunLock:
    """A lock file that survives a crash: a dead-pid or too-old lock is taken over."""

    def __init__(self, path: Path, stale_after: float) -> None:
        self.path = Path(path)
        self.stale_after = float(stale_after)

    def _read(self) -> Optional[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def is_held(self) -> bool:
        data = self._read()
        if not data:
            return False
        pid = int(data.get("pid", 0))
        ts = float(data.get("ts", 0))
        if not _pid_alive(pid):
            return False
        return (time.time() - ts) <= self.stale_after

    def acquire(self, pid: int) -> bool:
        if self.is_held():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"pid": int(pid), "ts": time.time()}), encoding="utf-8")
        return True

    def release(self) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink()


class RunHistory:
    """Persisted run records in .cache/ (git-ignored): timestamps + last result."""

    def __init__(self, path: Path, tz: Any = None) -> None:
        self.path = Path(path)
        self.tz = tz or get_tz()

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def _save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items[-500:]), encoding="utf-8")

    def record(self, result: RunResult) -> None:
        items = self._load()
        items.append(asdict(result))
        self._save(items)

    def last(self) -> Optional[dict]:
        items = self._load()
        return items[-1] if items else None

    def last_started(self) -> Optional[float]:
        last = self.last()
        return float(last["started_at"]) if last else None

    def count_today(self, now: Optional[float] = None) -> int:
        """Runs started on the current IST calendar day."""
        now = time.time() if now is None else now
        today = datetime.fromtimestamp(now, tz=self.tz).date()
        count = 0
        for item in self._load():
            started = item.get("started_at")
            if started is None:
                continue
            if datetime.fromtimestamp(float(started), tz=self.tz).date() == today:
                count += 1
        return count


class RunManager:
    """Spawns and supervises one `main.py` subprocess at a time."""

    def __init__(
        self,
        *,
        main_path: Path,
        lock_path: Path,
        history_path: Path,
        python: str,
        timeout: int,
        min_interval: int,
        max_per_day: int,
        ensure_disconnected: Optional[Callable[[], Any]] = None,
        tz: Any = None,
        log: Optional[logging.Logger] = None,
    ) -> None:
        self.main_path = Path(main_path)
        self._python = python
        self.timeout = int(timeout)
        self.min_interval = int(min_interval)
        self.max_per_day = int(max_per_day)
        self._ensure_disconnected = ensure_disconnected
        self._lock = RunLock(lock_path, stale_after=self.timeout + 120)
        self._history = RunHistory(history_path, tz=tz)
        self._log = log or _log
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._capture = False
        self._watchdog: Optional[asyncio.Task] = None
        self._started_at: Optional[float] = None
        self._timed_out = False
        self._stopped = False
        self._run_timeout: int = self.timeout

    # -- state -------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def history(self) -> RunHistory:
        return self._history

    def check_limits(self, now: Optional[float] = None) -> Optional[str]:
        """Return a human reason if a run must be refused, else None."""
        now = time.time() if now is None else now
        last = self._history.last_started()
        if last is not None and (now - last) < self.min_interval:
            remaining = int(self.min_interval - (now - last))
            return f"एक run के बाद कम से कम {self.min_interval // 60} मिनट रुकें ({remaining}s बाकी)।"
        if self._history.count_today(now) >= self.max_per_day:
            return f"आज की सीमा ({self.max_per_day} runs/दिन) पूरी हो गई।"
        return None

    # -- lifecycle ---------------------------------------------------------
    async def start(self, argv: list[str], capture: bool = False,
                    timeout: Optional[int] = None) -> "Refusal | None":
        """Start a run. Returns None on success, or a Refusal with the reason.

        With ``capture=True`` the child's stdout/stderr are captured (used by
        /preview to read the dry-run counts). ``timeout`` overrides the default
        hard timeout for this run only (a full-history scrape gets much longer).
        """
        if self.running:
            return Refusal("एक run पहले से चल रहा है। /stop से रोकें।")
        refusal = self.check_limits()
        if refusal:
            return Refusal(refusal)
        # Never let our own user client hold the SQLite session while main.py runs.
        if self._ensure_disconnected is not None:
            with contextlib.suppress(Exception):
                await self._ensure_disconnected()
        if not self._lock.acquire(os.getpid()):
            return Refusal("run lock किसी और process के पास है।")
        full_argv = [self._python, str(self.main_path), *argv]
        try:
            self._capture = capture
            self._proc = await asyncio.create_subprocess_exec(
                *full_argv,
                stdout=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL,
                stdin=asyncio.subprocess.DEVNULL,
                start_new_session=True,  # own process group -> we can kill the whole tree
                cwd=str(self.main_path.parent),
            )
        except OSError as exc:
            self._lock.release()
            self._proc = None
            return Refusal(f"run शुरू नहीं हो सका: {type(exc).__name__}")
        self._started_at = time.time()
        self._timed_out = False
        self._stopped = False
        self._run_timeout = int(timeout) if timeout else self.timeout
        self._watchdog = asyncio.create_task(self._timeout_watch())
        self._log.info("run started (pid %s)", self._proc.pid)
        return None

    async def wait(self) -> RunResult:
        """Await the process, record the result, release the lock."""
        proc = self._proc
        if proc is None:
            raise RuntimeError("no run in progress")
        output = ""
        if self._capture:
            stdout, stderr = await proc.communicate()
            exit_code = proc.returncode
            output = (stdout or b"").decode("utf-8", "replace") + (stderr or b"").decode("utf-8", "replace")
        else:
            exit_code = await proc.wait()
        if self._watchdog is not None:
            self._watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog
            self._watchdog = None
        if self._timed_out:
            result = RESULT_TIMEOUT
        elif self._stopped:
            result = RESULT_STOPPED
        elif exit_code == 0:
            result = RESULT_OK
        else:
            result = RESULT_FAILED
        started = self._started_at or time.time()
        finished = time.time()
        run = RunResult(started, finished, exit_code, result, finished - started, output)
        self._history.record(run)
        self._lock.release()
        self._proc = None
        self._started_at = None
        self._log.info("run finished: %s (%.0fs)", result, run.duration)
        return run

    async def stop(self) -> bool:
        """Kill the whole process tree of the running scrape. True if one was running."""
        if not self.running:
            return False
        self._stopped = True
        await self._kill_tree()
        return True

    async def _timeout_watch(self) -> None:
        limit = self._run_timeout
        try:
            await asyncio.sleep(limit)
        except asyncio.CancelledError:
            return
        if self.running:
            self._timed_out = True
            self._log.warning("run exceeded the %ss timeout; killing the process tree", limit)
            await self._kill_tree()

    async def _kill_tree(self) -> None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            with contextlib.suppress(Exception):
                await proc.wait()
