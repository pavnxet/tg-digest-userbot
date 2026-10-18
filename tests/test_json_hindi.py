"""The JSON output must keep Hindi as Hindi (no \\uXXXX escapes)."""
import json

from formatter import build_digest, dumps_compact, dumps_pretty, normalize_text


def _digest():
    channels = [
        {
            "name": "हिंदी समाचार",
            "username": "hindinews",
            "type": "channel",
            "link_base": "https://t.me/hindinews/",
            "count": 1,
            "messages": [
                {"id": 1, "t": "2026-10-18 09:14", "text": "नमस्ते! आज की ताज़ा खबर।"},
            ],
        }
    ]
    meta = {"part": "1/1"}
    return build_digest("सारांश दें", meta, channels)


def test_hindi_is_not_escaped_in_compact_json():
    text = dumps_compact(_digest())
    assert "नमस्ते" in text
    assert "\\u" not in text


def test_hindi_is_not_escaped_in_pretty_json():
    text = dumps_pretty(_digest())
    assert "आज की ताज़ा खबर" in text
    assert "\\u" not in text


def test_roundtrip_preserves_hindi():
    text = dumps_compact(_digest())
    parsed = json.loads(text)
    assert parsed["channels"][0]["messages"][0]["text"] == "नमस्ते! आज की ताज़ा खबर।"


def test_compact_output_has_no_extra_spaces():
    text = dumps_compact({"a": 1, "b": 2})
    assert text == '{"a":1,"b":2}'


def test_empty_and_false_fields_are_dropped():
    text = dumps_compact({"text": "hi", "media": None, "edited": False, "links": []})
    assert text == '{"text":"hi"}'


def test_normalize_text_collapses_blanks_but_keeps_words():
    raw = "line one   \n\n\n\nline two    with  spaces"
    cleaned = normalize_text(raw)
    assert cleaned == "line one\n\nline two with spaces"
