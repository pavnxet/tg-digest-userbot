"""Splitting: every part must be valid, standalone JSON."""
import json

from formatter import build_digest, dumps_compact
from splitter import split_digest


def _channel(name, count, text_size=40, username="ch"):
    messages = [
        {"id": 1000 + i, "t": "2026-10-18 09:00", "text": "x" * text_size}
        for i in range(count)
    ]
    return {
        "name": name,
        "username": username,
        "type": "channel",
        "link_base": f"https://t.me/{username}/",
        "count": count,
        "messages": messages,
    }


def _meta():
    return {
        "generated_at": "2026-10-18 18:50 IST",
        "timezone": "Asia/Kolkata",
        "from": "2026-10-18 00:00",
        "to": "2026-10-18 18:50",
        "channels_ok": 0,
        "messages_total": 0,
        "part": "1/1",
    }


def test_small_digest_is_a_single_part():
    channels = [_channel("A", 3), _channel("B", 3)]
    parts = split_digest("instr", _meta(), channels, max_chars=300_000)
    assert len(parts) == 1
    assert parts[0]["meta"]["part"] == "1/1"


def test_large_digest_splits_into_valid_parts():
    channels = [_channel(f"C{i}", 50) for i in range(8)]
    parts = split_digest("instructions", _meta(), channels, max_chars=4000)

    assert len(parts) > 1
    for index, part in enumerate(parts, start=1):
        # Each part must round-trip through json.loads on its own.
        text = dumps_compact(part)
        parsed = json.loads(text)
        assert parsed["ai_instructions"] == "instructions"
        assert parsed["meta"]["part"] == f"{index}/{len(parts)}"
        assert len(text) <= 4000 + 200  # small tolerance for the part label


def test_no_channel_is_lost_across_parts():
    channels = [_channel(f"C{i}", 30) for i in range(6)]
    parts = split_digest("i", _meta(), channels, max_chars=3000)
    seen = [ch["name"] for part in parts for ch in part["channels"]]
    assert sorted(seen) == sorted(ch["name"] for ch in channels)


def test_oversized_single_channel_splits_and_repeats_header():
    channels = [_channel("Big", 400, text_size=60)]
    parts = split_digest("i", _meta(), channels, max_chars=3000)

    assert len(parts) > 1
    for part in parts:
        assert len(part["channels"]) == 1
        channel = part["channels"][0]
        assert channel["name"] == "Big"
        assert channel["link_base"] == "https://t.me/ch/"
        assert channel["count"] == len(channel["messages"])
        json.loads(dumps_compact(part))  # valid on its own


def test_empty_channels_still_produces_one_part():
    parts = split_digest("i", _meta(), [], max_chars=1000)
    assert len(parts) == 1
    assert parts[0]["channels"] == []
