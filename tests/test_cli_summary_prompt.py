"""CLI: --summary-prompt and --summary-prompt-file (section 5)."""
import json
from datetime import datetime, timezone

from conftest import run_main
from fake_client import make_channel, make_message
from formatter import dumps_compact
from prompts import load_prompt


def _fake_with(fake, n_channels=1, n_messages=1):
    for i in range(n_channels):
        channel = make_channel(1000 + i, f"Chan{i}", username=f"chan{i}")
        messages = [make_message(j + 1, datetime.now(timezone.utc), text="hello world " * 20) for j in range(n_messages)]
        fake.add_channel(channel, messages)
    return fake


def _digest(project):
    files = sorted(project["out"].glob("*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8"))


def test_summary_prompt_embeds_the_named_prompt(project, fake_client, monkeypatch):
    _fake_with(fake_client, 1, 2)
    rc = run_main(monkeypatch, project, fake_client, ["--last", "1h", "--summary-prompt", "detailed"], "@chan0\n")
    assert rc == 0
    digest = _digest(project)
    assert digest["ai_instructions"] == load_prompt("detailed")
    assert not digest["ai_instructions"].startswith("#")   # title line stripped


def test_default_prompt_used_without_the_flag(project, fake_client, monkeypatch):
    _fake_with(fake_client)
    rc = run_main(monkeypatch, project, fake_client, ["--last", "1h"], "@chan0\n")
    assert rc == 0
    assert _digest(project)["ai_instructions"] == load_prompt("default")


def test_prompt_file_wins_over_name(project, fake_client, monkeypatch, tmp_path, capsys):
    _fake_with(fake_client)
    prompt_file = tmp_path / "custom.md"
    prompt_file.write_text("CUSTOM PROMPT TEXT", encoding="utf-8")
    rc = run_main(monkeypatch, project, fake_client,
                  ["--last", "1h", "--summary-prompt", "detailed",
                   "--summary-prompt-file", str(prompt_file)], "@chan0\n")
    assert rc == 0
    assert _digest(project)["ai_instructions"] == "CUSTOM PROMPT TEXT"
    assert "overrides" in capsys.readouterr().out


def test_unknown_prompt_name_exits_early_with_the_list(project, fake_client, monkeypatch, capsys):
    _fake_with(fake_client)
    rc = run_main(monkeypatch, project, fake_client, ["--last", "1h", "--summary-prompt", "nope"], "@chan0\n")
    assert rc == 2
    out = capsys.readouterr().out
    assert "Available prompts:" in out and "detailed" in out
    assert not list(project["out"].glob("*.json"))       # nothing written


def test_every_split_part_has_ai_instructions_and_respects_max_chars(project, fake_client, monkeypatch):
    _fake_with(fake_client, n_channels=6, n_messages=25)
    channels_text = "\n".join(f"@chan{i}" for i in range(6)) + "\n"
    rc = run_main(monkeypatch, project, fake_client,
                  ["--last", "1h", "--max-chars", "5000"], channels_text)
    assert rc == 0
    parts = sorted(project["out"].glob("*.json"))
    assert len(parts) >= 2
    for path in parts:
        part = json.loads(path.read_text(encoding="utf-8"))
        assert part["ai_instructions"] == load_prompt("default")
        assert len(dumps_compact(part)) <= 5000


def test_empty_prompt_file_falls_back_to_default(project, fake_client, monkeypatch, tmp_path, capsys):
    _fake_with(fake_client)
    empty = tmp_path / "empty.md"
    empty.write_text("   \n", encoding="utf-8")
    rc = run_main(monkeypatch, project, fake_client,
                  ["--last", "1h", "--summary-prompt-file", str(empty)], "@chan0\n")
    assert rc == 0
    assert _digest(project)["ai_instructions"] == load_prompt("default")
    assert "empty" in capsys.readouterr().out
