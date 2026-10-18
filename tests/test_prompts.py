"""Prompt files and the loader (section 5)."""
import json
import re
from pathlib import Path

import pytest

from prompts import (
    MAX_PROMPT_CHARS,
    PromptError,
    available_names,
    default_prompt,
    list_prompts,
    load_prompt,
)

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "digest.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

EXPECTED = {"default", "detailed", "exam_alerts", "news_brief", "merge_parts", "ask"}

TOP = set(SCHEMA["properties"].keys())
META = set(SCHEMA["properties"]["meta"]["properties"].keys())
CHAN = set(SCHEMA["properties"]["channels"]["items"]["properties"].keys())
MSG = set(SCHEMA["definitions"]["message"]["properties"].keys())

_TOKEN_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]*(?:\[\])?(?:\.[A-Za-z_][A-Za-z0-9_]*)*)`")


def _is_known(token: str) -> bool:
    parts = [p.replace("[]", "") for p in token.split(".")]
    head = parts[0]
    if head == "meta":
        return len(parts) == 1 or all(p in META for p in parts[1:])
    if head == "channels":
        return len(parts) == 1 or all(p in CHAN for p in parts[1:])
    if head in TOP:
        return len(parts) == 1
    return head in META or head in CHAN or head in MSG


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
def test_all_prompt_files_exist():
    assert set(available_names()) == EXPECTED


def test_each_file_has_a_valid_title_line_and_is_utf8_and_small():
    for info in list_prompts():
        raw = (Path(__file__).resolve().parent.parent / "prompts" / f"{info.name}.md").read_text(encoding="utf-8")
        assert raw.splitlines()[0].startswith(f"# {info.name} - "), info.name
        assert len(raw) <= MAX_PROMPT_CHARS, info.name
        raw.encode("utf-8")  # valid UTF-8 or this raises


def test_each_prompt_forbids_untrusted_instructions():
    for name in EXPECTED:
        assert "untrusted data" in load_prompt(name).lower(), name


def test_each_prompt_asks_for_hindi():
    for name in EXPECTED:
        text = load_prompt(name)
        assert "Hindi" in text and "Devanagari" in text, name


# --------------------------------------------------------------------------
# Schema consistency
# --------------------------------------------------------------------------
def test_prompt_field_names_match_the_schema():
    unknown = {}
    for name in EXPECTED:
        text = load_prompt(name)
        bad = [t for t in _TOKEN_RE.findall(text) if not _is_known(t)]
        if bad:
            unknown[name] = bad
    assert not unknown, f"prompts name fields the schema does not have: {unknown}"


def test_known_fields_are_present_in_the_schema():
    for field in ("from", "to", "skipped", "truncated_channels", "part"):
        assert field in META
    for field in ("id", "t", "text", "author", "reply_to", "fwd", "edited", "media", "poll", "links"):
        assert field in MSG
    for field in ("name", "link_base", "count", "truncated", "messages"):
        assert field in CHAN


# --------------------------------------------------------------------------
# Loader
# --------------------------------------------------------------------------
def test_load_prompt_strips_the_title_line():
    text = load_prompt("default")
    assert not text.startswith("#")
    assert "This file is JSON" in text


def test_list_prompts_is_sorted_and_described():
    infos = list_prompts()
    assert [i.name for i in infos] == sorted(EXPECTED)
    assert all(i.description for i in infos)


@pytest.mark.parametrize("bad", ["", "nope", "../x", "a/b", "/etc/passwd", "Foo", "..", "x" * 41, "a b"])
def test_bad_names_are_rejected(bad):
    with pytest.raises(PromptError):
        load_prompt(bad)


def test_symlink_escaping_the_folder_is_rejected(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# evil - outside\nsecret", encoding="utf-8")
    root = tmp_path / "prompts"
    root.mkdir()
    try:
        (root / "evil.md").symlink_to(outside)
    except OSError:
        pytest.skip("filesystem without symlinks")
    with pytest.raises(PromptError):
        load_prompt("evil", directory=root)


def test_oversize_file_is_refused(tmp_path):
    root = tmp_path / "prompts"
    root.mkdir()
    (root / "big.md").write_text("# big - too large\n" + "x" * (MAX_PROMPT_CHARS + 1), encoding="utf-8")
    with pytest.raises(PromptError):
        load_prompt("big", directory=root)


def test_missing_default_falls_back(tmp_path):
    empty = tmp_path / "prompts"
    empty.mkdir()
    assert default_prompt("BUILT-IN-FALLBACK", directory=empty) == "BUILT-IN-FALLBACK"
    assert default_prompt("BUILT-IN-FALLBACK") != "BUILT-IN-FALLBACK"  # real file exists
