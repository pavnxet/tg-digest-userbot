"""Phase 3: formatter — schema, encoding, formats, token estimate."""
import json
from pathlib import Path

import jsonschema

from formatter import (
    build_digest,
    dumps_compact,
    dumps_pretty,
    estimate_tokens,
    ist_str,
    normalize_text,
    to_jsonl,
    to_txt,
)

ROOT = Path(__file__).resolve().parent.parent


def _sample_digest(ai="instructions here"):
    channels = [
        {
            "name": "हिंदी समाचार",
            "username": "hindinews",
            "type": "channel",
            "link_base": "https://t.me/hindinews/",
            "count": 2,
            "messages": [
                {"id": 1, "t": "2026-10-18 09:14", "text": "नमस्ते! आज की खबर।", "links": ["https://x"]},
                {"id": 2, "t": "2026-10-18 09:20", "text": "hello", "reply_to": 1, "media": "photo"},
            ],
        }
    ]
    meta = {
        "generated_at": "2026-10-18 18:50 IST",
        "timezone": "Asia/Kolkata",
        "from": "2026-10-18 00:00",
        "to": "2026-10-18 18:50",
        "channels_ok": 1,
        "messages_total": 2,
        "part": "1/1",
    }
    return build_digest(ai, meta, channels)


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
def test_digest_matches_json_schema(schema):
    jsonschema.validate(json.loads(dumps_compact(_sample_digest())), schema)


def test_digest_with_skipped_matches_schema(schema):
    digest = _sample_digest()
    digest["meta"]["skipped"] = [{"channel": "x", "reason": "not_joined"}]
    jsonschema.validate(json.loads(dumps_compact(digest)), schema)


def test_digest_with_truncation_matches_schema(schema):
    digest = _sample_digest()
    digest["channels"][0]["truncated"] = True
    digest["channels"][0]["cap"] = 10
    digest["meta"]["truncated_channels"] = ["हिंदी समाचार"]
    jsonschema.validate(json.loads(dumps_compact(digest)), schema)


# --------------------------------------------------------------------------
# Encoding / compactness
# --------------------------------------------------------------------------
def test_hindi_not_escaped_and_no_unicode_escapes():
    text = dumps_compact(_sample_digest())
    assert "नमस्ते" in text and "\\u" not in text


def test_compact_separators():
    assert dumps_compact({"a": 1, "b": 2}) == '{"a":1,"b":2}'


def test_empty_false_null_fields_dropped():
    assert dumps_compact({"t": "x", "media": None, "edited": False, "links": []}) == '{"t":"x"}'


def test_ai_instructions_is_first_key():
    assert dumps_compact(_sample_digest()).startswith('{"ai_instructions"')


def test_round_trip_preserves_hindi():
    parsed = json.loads(dumps_compact(_sample_digest()))
    assert parsed["channels"][0]["messages"][0]["text"] == "नमस्ते! आज की खबर।"


def test_pretty_uses_indent_and_still_no_escapes():
    text = dumps_pretty(_sample_digest())
    assert "\n" in text and "  " in text
    assert "नमस्ते" in text and "\\u" not in text


def test_compact_is_default_and_smaller_than_pretty():
    assert len(dumps_compact(_sample_digest())) < len(dumps_pretty(_sample_digest()))


# --------------------------------------------------------------------------
# BUG-04 (fixed): the empty-prompt fallback is a main-level concern; see
# test_phase6_cli.py::test_empty_prompt_file_falls_back_to_default.
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# JSONL / txt
# --------------------------------------------------------------------------
def test_jsonl_every_line_is_valid_json():
    text = to_jsonl(_sample_digest())
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 3  # 1 meta + 2 messages
    for line in lines:
        json.loads(line)
    meta_line = json.loads(lines[0])
    assert "ai_instructions" in meta_line and "meta" in meta_line
    assert json.loads(lines[1])["channel"] == "हिंदी समाचार"


def test_txt_format():
    text = to_txt(_sample_digest())
    assert text.startswith("== हिंदी समाचार (https://t.me/hindinews/) ==")
    assert "[09:14] #1 नमस्ते! आज की खबर।" in text


# --------------------------------------------------------------------------
# Token estimate
# --------------------------------------------------------------------------
def test_estimate_labels_and_devanagari_higher():
    est = estimate_tokens("क" * 300)
    assert est["est_tokens_devanagari"] > est["est_tokens_latin"]
    assert est["devanagari_ratio"] > 0.15
    assert est["est_tokens_primary"] == est["est_tokens_devanagari"]


def test_estimate_latin_for_latin_text():
    est = estimate_tokens("a" * 400)
    assert est["est_tokens_latin"] == 100
    assert est["est_tokens_primary"] == est["est_tokens_latin"]


def test_main_output_labels_estimates_as_estimated():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert src.count("estimated tokens") >= 2


# --------------------------------------------------------------------------
# normalize_text / ist_str helpers
# --------------------------------------------------------------------------
def test_normalize_collapses_blanks_keeps_words():
    assert normalize_text("a   b\n\n\n\nc  ") == "a b\n\nc"


def test_ist_str_renders_in_ist():
    from datetime import datetime, timezone

    assert ist_str(datetime(2026, 10, 18, 3, 44, tzinfo=timezone.utc)) == "2026-10-18 09:14"
