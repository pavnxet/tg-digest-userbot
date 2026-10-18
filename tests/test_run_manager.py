"""Section A: run manager (lock, limits, subprocess, timeout, kill)."""
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime

import pytest

from run_manager import (
    RESULT_OK,
    RESULT_STOPPED,
    RESULT_TIMEOUT,
    Refusal,
    RunHistory,
    RunLock,
    RunManager,
    RunResult,
)
from timeutil import get_tz

# A fixed mid-IST-day timestamp, so day-boundary rollover never bites the tests.
NOON_IST = datetime(2026, 10, 18, 12, 0, tzinfo=get_tz()).timestamp()

ECHO_SCRIPT = "import sys, json, pathlib\npathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n"
SLEEP_SCRIPT = "import time\ntime.sleep(30)\n"


def _write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _manager(tmp_path, script, **overrides):
    kwargs = dict(
        main_path=script,
        lock_path=tmp_path / "run.lock",
        history_path=tmp_path / "history.json",
        python=sys.executable,
        timeout=30,
        min_interval=300,
        max_per_day=12,
    )
    kwargs.update(overrides)
    return RunManager(**kwargs)


# --------------------------------------------------------------------------
# Lock
# --------------------------------------------------------------------------
def test_lock_acquire_and_release(tmp_path):
    lock = RunLock(tmp_path / "run.lock", stale_after=999)
    assert lock.acquire(os.getpid()) is True       # a live pid -> genuinely held
    assert lock.is_held() is True
    assert lock.acquire(999) is False              # already held
    lock.release()
    assert lock.is_held() is False


def test_lock_is_stale_when_pid_is_dead(tmp_path):
    dead = subprocess.Popen([sys.executable, "-c", ""])
    dead.wait()
    path = tmp_path / "run.lock"
    path.write_text(json.dumps({"pid": dead.pid, "ts": time.time()}), encoding="utf-8")
    lock = RunLock(path, stale_after=999)
    assert lock.is_held() is False
    assert lock.acquire(1) is True


def test_lock_is_stale_when_too_old(tmp_path):
    path = tmp_path / "run.lock"
    path.write_text(json.dumps({"pid": __import__("os").getpid(), "ts": time.time() - 10_000}), encoding="utf-8")
    assert RunLock(path, stale_after=60).is_held() is False


# --------------------------------------------------------------------------
# History + limits
# --------------------------------------------------------------------------
def test_history_records_and_counts(tmp_path):
    history = RunHistory(tmp_path / "h.json")
    now = time.time()
    history.record(RunResult(now, now + 5, 0, RESULT_OK, 5))
    assert history.last_started() == now
    assert history.count_today(now) == 1
    assert history.last()["result"] == RESULT_OK


def test_history_survives_corruption(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("{ not json", encoding="utf-8")
    history = RunHistory(path)
    assert history.count_today() == 0
    assert history.last() is None


def test_min_interval_refusal(tmp_path):
    script = _write(tmp_path, "s.py", SLEEP_SCRIPT)
    manager = _manager(tmp_path, script)
    manager.history.record(RunResult(NOON_IST, NOON_IST, 0, RESULT_OK, 0))
    assert manager.check_limits(NOON_IST + 10) is not None     # too soon
    assert manager.check_limits(NOON_IST + 400) is None        # fine


def test_daily_cap_refusal(tmp_path):
    script = _write(tmp_path, "s.py", SLEEP_SCRIPT)
    manager = _manager(tmp_path, script, max_per_day=3)
    for _ in range(3):
        manager.history.record(RunResult(NOON_IST, NOON_IST, 0, RESULT_OK, 0))
    assert manager.check_limits(NOON_IST + 400) is not None    # 3 runs today, cap is 3


# --------------------------------------------------------------------------
# Spawning
# --------------------------------------------------------------------------
def test_start_runs_subprocess_and_reports_ok(tmp_path):
    script = _write(tmp_path, "s.py", ECHO_SCRIPT)
    out = tmp_path / "argv.json"
    manager = _manager(tmp_path, script)

    async def scenario():
        refusal = await manager.start([str(out), "--today"])
        assert refusal is None
        assert manager.running is True
        result = await manager.wait()
        return result

    result = asyncio.run(scenario())
    assert result.result == RESULT_OK and result.exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8")) == ["--today"]
    assert manager.running is False
    assert not (tmp_path / "run.lock").exists()          # lock released


def test_argv_is_a_list_and_shell_is_never_used(tmp_path):
    script = _write(tmp_path, "s.py", ECHO_SCRIPT)
    out = tmp_path / "argv.json"
    manager = _manager(tmp_path, script)
    nasty = ["--since", "2026-01-01; touch /tmp/pwned && rm -rf x", "--auto-join"]

    async def scenario():
        await manager.start([str(out), *nasty])
        await manager.wait()

    asyncio.run(scenario())
    assert json.loads(out.read_text(encoding="utf-8")) == nasty  # literal, not interpreted


def test_ensure_disconnected_called_before_spawn(tmp_path):
    script = _write(tmp_path, "s.py", ECHO_SCRIPT)
    out = tmp_path / "argv.json"
    calls = []

    async def disconnect():
        calls.append("disconnected")

    manager = _manager(tmp_path, script, ensure_disconnected=disconnect)

    async def scenario():
        await manager.start([str(out)])
        await manager.wait()

    asyncio.run(scenario())
    assert calls == ["disconnected"]


def test_start_refused_when_already_running(tmp_path):
    script = _write(tmp_path, "s.py", SLEEP_SCRIPT)
    manager = _manager(tmp_path, script)

    async def scenario():
        assert await manager.start([]) is None
        refusal = await manager.start([])
        assert isinstance(refusal, Refusal)
        await manager.stop()
        await manager.wait()

    asyncio.run(scenario())


def test_stop_kills_the_process_tree(tmp_path):
    script = _write(tmp_path, "s.py", SLEEP_SCRIPT)
    manager = _manager(tmp_path, script)

    async def scenario():
        await manager.start([])
        assert await manager.stop() is True
        return await manager.wait()

    result = asyncio.run(scenario())
    assert result.result == RESULT_STOPPED
    assert manager.running is False


def test_timeout_kills_the_process(tmp_path):
    script = _write(tmp_path, "s.py", SLEEP_SCRIPT)
    manager = _manager(tmp_path, script, timeout=1)

    async def scenario():
        await manager.start([])
        return await manager.wait()

    result = asyncio.run(scenario())
    assert result.result == RESULT_TIMEOUT
    assert manager.running is False
