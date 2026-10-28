"""Section B: channels.txt syntax, file-safe editing, and main.py paused handling."""
from datetime import datetime, timezone

import pytest

from channel_store import ChannelError, ChannelStore, render_line
from channels import parse_channel_line, parse_channels_file
from fake_client import make_channel, make_message
from conftest import run_main


# --------------------------------------------------------------------------
# Parser: new syntax, old lines unchanged
# --------------------------------------------------------------------------
def test_old_line_still_parses():
    spec = parse_channel_line("@durov | My Label")
    assert spec.kind == "username" and spec.value == "durov" and spec.label == "My Label"
    assert spec.tags == () and spec.paused is False


def test_label_with_pipe_still_works():
    assert parse_channel_line("@ex | a|b").label == "a|b"


def test_tags_and_off():
    spec = parse_channel_line("@news | Daily | tags=news,jobs | off")
    assert spec.label == "Daily" and spec.tags == ("news", "jobs") and spec.paused is True


def test_off_without_label():
    spec = parse_channel_line("@news | off")
    assert spec.paused is True and spec.label is None


def test_duplicate_tags_collapse():
    assert parse_channel_line("@news | tags=a,a,b").tags == ("a", "b")


def test_render_line_round_trips():
    assert render_line("@news", "Daily", ["news"], True) == "@news | Daily | tags=news | off"
    assert render_line("@news", None, [], False) == "@news"


def test_file_with_crlf_and_bom_parses(tmp_path):
    path = tmp_path / "channels.txt"
    path.write_bytes("\ufeff@a | A | tags=x\r\n@bbbb | off\r\n".encode("utf-8"))
    specs = parse_channels_file(path)
    assert specs[0].tags == ("x",) and specs[0].paused is False
    assert specs[1].paused is True


# --------------------------------------------------------------------------
# Store: editing preserves everything else
# --------------------------------------------------------------------------
def _store(tmp_path, content="", **kw):
    path = tmp_path / "channels.txt"
    if content:
        path.write_bytes(content.encode("utf-8"))
    return ChannelStore(path, tmp_path / "channels.lock", **kw), path


def test_entries_and_indices(tmp_path):
    store, _ = _store(tmp_path, "# c\n\n@a | A\n@bbbb | B\n")
    entries = store.entries()
    assert [e.index for e in entries] == [1, 2]
    assert entries[0].label == "A"


def test_add_appends_and_rejects_bad_input(tmp_path):
    store, path = _store(tmp_path, "@a\n")
    assert store.add("@bbbb | New").ok is True
    assert "@bbbb | New" in path.read_text(encoding="utf-8")
    for bad in ["", "   ", "@x\n@y", "@" + "z" * 250, "@a\x00b"]:
        with pytest.raises(ChannelError):
            store.add(bad)


def test_add_duplicate_by_target(tmp_path):
    store, _ = _store(tmp_path, "@news\n")
    result = store.add("https://t.me/news")
    assert result.ok is False and result.duplicate_index == 1


def test_add_duplicate_by_resolved_id(tmp_path):
    store, _ = _store(tmp_path, "@news | News\n", ids_path=tmp_path / "ids.json")
    store.remember_id("username:news", 12345)
    result = store.add("@othernews", resolved_id=12345)  # different raw, same id
    assert result.ok is False


def test_add_cap(tmp_path):
    store, _ = _store(tmp_path, "\n".join(f"@c{i}" for i in range(200)) + "\n")
    with pytest.raises(ChannelError):
        store.add("@one_more")


def test_remove_only_touches_that_line(tmp_path):
    store, path = _store(tmp_path, "# keep\n\n@a | A\n@bbbb | B\n# tail\n")
    store.remove(1)
    text = path.read_text(encoding="utf-8")
    assert "@a | A" not in text
    assert "# keep" in text and "@bbbb | B" in text and "# tail" in text


def test_pause_resume_tag_untag(tmp_path):
    store, path = _store(tmp_path, "@a | A\n")
    store.set_paused(1, True)
    assert parse_channel_line(path.read_text(encoding="utf-8").strip()).paused is True
    store.add_tag(1, "news")
    store.add_tag(1, "jobs")
    spec = parse_channel_line(path.read_text(encoding="utf-8").strip())
    assert spec.tags == ("news", "jobs") and spec.paused is True
    store.remove_tag(1, "news")
    store.set_paused(1, False)
    spec = parse_channel_line(path.read_text(encoding="utf-8").strip())
    assert spec.tags == ("jobs",) and spec.paused is False


def test_unaffected_lines_are_byte_for_byte(tmp_path):
    content = "# comment\r\n\r\n@a | Label A\r\n@bbbb | Label B | tags=news\r\n"
    store, path = _store(tmp_path, content)
    store.set_paused(1, True)
    after = path.read_bytes().decode("utf-8")
    assert not after.startswith("\ufeff")           # no BOM written
    assert "\r\n" in after                          # CRLF preserved
    assert "# comment" in after and "@bbbb | Label B | tags=news" in after
    assert (path.with_name(path.name + ".bak")).exists()   # backup kept


def test_crash_mid_write_leaves_original_intact(tmp_path, monkeypatch):
    import channel_store

    store, path = _store(tmp_path, "@a\n")
    original = path.read_text(encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(channel_store.os, "replace", boom)
    with pytest.raises(OSError):
        store.add("@bbbb")
    assert path.read_text(encoding="utf-8") == original


def test_edit_refused_while_run_active(tmp_path):
    store, _ = _store(tmp_path, "@a\n", is_run_active=lambda: True)
    with pytest.raises(ChannelError):
        store.add("@bbbb")


def test_edit_refused_when_lock_held(tmp_path):
    class HeldLock:
        def acquire(self, pid):
            return False

    store, _ = _store(tmp_path, "@a\n", lock_factory=lambda p: HeldLock())
    with pytest.raises(ChannelError):
        store.add("@bbbb")


def test_find_by_index_name_and_ambiguous(tmp_path):
    store, _ = _store(tmp_path, "@a | Dup\n@bbbb | Dup\n")
    assert [e.index for e in store.find("1")] == [1]
    assert [e.index for e in store.find("@bbbb")] == [2]
    assert [e.index for e in store.find("Dup")] == [1, 2]   # ambiguous
    assert store.find("nope") == []


# --------------------------------------------------------------------------
# main.py integration: a paused channel is reported as "paused"
# --------------------------------------------------------------------------
def test_paused_channel_reported_in_meta_skipped(project, fake_client, monkeypatch):
    ch = make_channel(1234567890, "News", username="news")
    fake_client.add_channel(ch, [make_message(1, datetime.now(timezone.utc), text="x")])
    rc = run_main(monkeypatch, project, fake_client, ["--last", "1h"], "@news | off\n")
    # Nothing was read, so the run must not report success (or advance state).
    assert rc == 1
    import json

    digest = json.loads(sorted(project["out"].glob("*.json"))[0].read_text(encoding="utf-8"))
    assert digest["meta"]["channels_ok"] == 0
    assert any(s["reason"] == "paused" for s in digest["meta"]["skipped"])
