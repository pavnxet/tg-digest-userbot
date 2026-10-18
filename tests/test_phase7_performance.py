"""Phase 7: performance, memory and call-count checks.

Run explicitly:  pytest tests/test_phase7_performance.py -s
"""
import time
import tracemalloc
from datetime import datetime, timedelta, timezone

import pytest

from conftest import run_main
from fake_client import make_channel, make_message
from timeutil import get_tz

IST = get_tz()
BASE = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def ist(dt):
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M")


WINDOW = ["--since", ist(BASE - timedelta(days=1)), "--until", ist(BASE + timedelta(minutes=1))]


def _history(n, text_len=60):
    step = timedelta(seconds=1)
    return [make_message(n - i, BASE - i * step, text="x" * text_len) for i in range(n)]


@pytest.fixture
def no_delay(monkeypatch):
    async def _no_sleep(seconds):
        return None

    monkeypatch.setattr("main.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("scraper.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("channels.asyncio.sleep", _no_sleep)


def _build_fake(fake_client, n_channels=50, per_channel=5000):
    lines = []
    for c in range(n_channels):
        cid = 100000 + c
        username = f"chan{c}"
        ch = make_channel(cid, f"Channel {c}", username=username)
        fake_client.add_channel(ch, _history(per_channel))
        lines.append(f"@{username}")
    return "\n".join(lines) + "\n"


def test_large_run_performance(project, fake_client, monkeypatch, no_delay):
    lines = _build_fake(fake_client, 50, 5000)
    tracemalloc.start()
    start = time.perf_counter()
    rc = run_main(monkeypatch, project, fake_client, WINDOW + ["--max-per-channel", "5000"], lines)
    elapsed = time.perf_counter() - start
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert rc == 0
    out_size = sum(f.stat().st_size for f in project["out"].glob("*.json"))
    print(f"\n[PERF] 50x5000: runtime={elapsed:.1f}s peak_mem={peak/1e6:.1f}MB output={out_size/1e6:.2f}MB "
          f"files={len(list(project['out'].glob('*.json')))}")
    print(f"[PERF] messages read (iter yields)={fake_client.iter_yielded}")


def test_no_per_message_entity_lookups(project, fake_client, monkeypatch, no_delay):
    lines = _build_fake(fake_client, 10, 200)
    run_main(monkeypatch, project, fake_client, WINDOW, lines)
    get_entity_calls = [c for c in fake_client.calls if c[0] == "get_entity"]
    iter_calls = [c for c in fake_client.calls if c[0] == "iter_messages"]
    print(f"\n[CALLS] get_entity={len(get_entity_calls)} iter_messages={len(iter_calls)} for 10 channels x 200 msgs")
    # One get_entity per channel (resolution), NOT one per message.
    assert len(get_entity_calls) <= 20
    assert len(iter_calls) == 10


def test_inter_channel_delay_floor(project, fake_client, monkeypatch):
    sleeps = []

    async def _record(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("main.asyncio.sleep", _record)
    monkeypatch.setattr("main.random.uniform", lambda a, b: 2.0)
    ch = make_channel(100000, "C", username="c")
    fake_client.add_channel(ch, _history(3))
    run_main(monkeypatch, project, fake_client, WINDOW, "@c\n@c2\n")  # 2 specs -> 1 ok channel
    # at least one inter-channel sleep of the configured size
    assert all(s == 2.0 for s in sleeps)
