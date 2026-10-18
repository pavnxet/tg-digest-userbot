"""Phase 3/4 (top-up): main.py internals — render, per-channel files, resume."""
from datetime import datetime, timezone

import pytest

import main
from conftest import run_main
from fake_client import make_channel, make_message
from timeutil import get_tz

IST = get_tz()
BASE = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def _channel_dict():
    return {
        "name": "News", "username": "news", "type": "channel",
        "link_base": "https://t.me/news/", "count": 1,
        "messages": [{"id": 1, "t": "2026-10-18 17:30", "text": "hello"}],
    }


def _digest():
    return {"ai_instructions": "i", "meta": {"part": "1/1"}, "channels": [_channel_dict()]}


# --------------------------------------------------------------------------
# render_part / write_outputs
# --------------------------------------------------------------------------
def test_render_part_all_formats():
    assert main.render_part(_digest(), "json", False).startswith('{"ai_instructions"')
    assert "\n" in main.render_part(_digest(), "json", True)
    assert main.render_part(_digest(), "jsonl", False).count("\n") >= 2
    assert "#1 hello" in main.render_part(_digest(), "txt", False)


def test_write_outputs_per_channel_files(tmp_path):
    from types import SimpleNamespace

    args = SimpleNamespace(output_dir=str(tmp_path), format="json", pretty=False,
                           per_channel_files=True, max_chars=300000)
    written = main.write_outputs(args, "i", {"part": "1/1"}, [_channel_dict(), _channel_dict()], IST)
    names = sorted(p.name for p, _ in written)
    assert len(written) == 2
    assert all(n.startswith("digest_") and n.endswith(".json") for n in names)


def test_write_outputs_splits_when_needed(tmp_path):
    from types import SimpleNamespace

    channels = [{**_channel_dict(), "messages": [{"id": i, "t": "2026-10-18 17:30", "text": "x" * 50} for i in range(100)]}]
    args = SimpleNamespace(output_dir=str(tmp_path), format="json", pretty=False,
                           per_channel_files=False, max_chars=3000)
    written = main.write_outputs(args, "i", {"part": "1/1"}, channels, IST)
    assert len(written) > 1
    assert any("part" in p.name for p, _ in written)


# --------------------------------------------------------------------------
# Checkpoint
# --------------------------------------------------------------------------
def test_checkpoint_round_trip(tmp_path):
    cp = main.Checkpoint(tmp_path / "run.jsonl")
    assert cp.load_done() == {}
    cp.append_channel("@a", _channel_dict())
    cp.append_skip("@b", "not_joined")
    done = cp.load_done()
    assert "@a" in done and done["@a"]["name"] == "News"
    assert "@b" not in done


def test_checkpoint_ignores_corrupt_lines(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text("not json\n" + '{"type":"channel","key":"@a","channel":{"name":"N"}}\n', encoding="utf-8")
    done = main.Checkpoint(path).load_done()
    assert "@a" in done


# --------------------------------------------------------------------------
# End-to-end formats and resume
# --------------------------------------------------------------------------
@pytest.mark.parametrize("fmt,ext", [("json", "json"), ("jsonl", "jsonl"), ("txt", "txt")])
def test_end_to_end_formats(project, fake_client, monkeypatch, fmt, ext):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, [make_message(1, BASE, text="hello")])
    run_main(monkeypatch, project, fake_client, ["--last", "1h", "--format", fmt], "@news\n")
    files = list(project["out"].glob(f"*.{ext}"))
    assert files, f"no .{ext} file written"


def test_resume_does_not_duplicate_done_channels(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, [make_message(1, BASE, text="hello")])
    run_main(monkeypatch, project, fake_client, ["--last", "1h"], "@news\n")
    first_yielded = fake_client.iter_yielded

    fake_client.iter_yielded = 0
    run_main(monkeypatch, project, fake_client, ["--last", "1h", "--resume"], "@news\n")
    # Resumed run must reuse the checkpoint, not re-read the channel.
    assert fake_client.iter_yielded == 0
    assert first_yielded >= 1
