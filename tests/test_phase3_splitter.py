"""Phase 3: splitter — sizes, validity, no loss, naming."""
import json

from formatter import build_digest, dumps_compact
from main import _safe_name
from splitter import split_digest


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


def _channel(name, n, size=40, base=0):
    return {
        "name": name,
        "username": "ch",
        "type": "channel",
        "link_base": "https://t.me/ch/",
        "count": n,
        "messages": [{"id": base + i, "t": "2026-10-18 09:00", "text": "x" * size} for i in range(n)],
    }


def _all_ids(parts):
    return [m["id"] for p in parts for ch in p["channels"] for m in ch["messages"]]


def test_below_limit_is_one_part():
    parts = split_digest("i", _meta(), [_channel("A", 3), _channel("B", 3)], 300_000)
    assert len(parts) == 1 and parts[0]["meta"]["part"] == "1/1"


def test_large_splits_into_valid_parts():
    channels = [_channel(f"C{i}", 50) for i in range(8)]
    parts = split_digest("instr", _meta(), channels, 4000)
    assert len(parts) > 1
    for i, part in enumerate(parts, 1):
        parsed = json.loads(dumps_compact(part))
        assert parsed["ai_instructions"] == "instr"
        assert parsed["meta"]["part"] == f"{i}/{len(parts)}"


def test_no_message_lost_or_duplicated():
    channels = [_channel(f"C{i}", 30, base=i * 100) for i in range(6)]
    original_ids = sorted(m["id"] for ch in channels for m in ch["messages"])
    parts = split_digest("i", _meta(), channels, 3000)
    assert sorted(_all_ids(parts)) == original_ids
    assert len(_all_ids(parts)) == len(set(_all_ids(parts)))


def test_single_channel_over_limit_splits_and_repeats_header():
    parts = split_digest("i", _meta(), [_channel("Big", 400, size=60)], 3000)
    assert len(parts) > 1
    for part in parts:
        assert len(part["channels"]) == 1
        assert part["channels"][0]["name"] == "Big"
        assert part["channels"][0]["link_base"] == "https://t.me/ch/"
        assert part["channels"][0]["count"] == len(part["channels"][0]["messages"])


def test_single_message_larger_than_limit_is_not_dropped_and_no_hang():
    # A message that alone exceeds max_chars must still appear (never split inside).
    channels = [_channel("Huge", 3, size=10_000)]
    parts = split_digest("i", _meta(), channels, 2000)
    assert sorted(_all_ids(parts)) == sorted(m["id"] for ch in channels for m in ch["messages"])
    # Every part is still valid JSON.
    for part in parts:
        json.loads(dumps_compact(part))


def test_empty_channels_still_one_part():
    parts = split_digest("i", _meta(), [], 1000)
    assert len(parts) == 1 and parts[0]["channels"] == []


def test_exactly_at_limit_prefers_single_part_when_it_fits():
    # Build data whose serialized size is just under the limit.
    channels = [_channel("A", 10)]
    size = len(dumps_compact(build_digest("i", _meta(), channels)))
    parts = split_digest("i", _meta(), channels, size + 200)
    assert len(parts) == 1


# --------------------------------------------------------------------------
# Safe file names (--per-channel-files)
# --------------------------------------------------------------------------
def test_safe_name_handles_slashes_colons_emoji_and_length():
    assert "/" not in _safe_name("a/b:c")
    assert ":" not in _safe_name("a/b:c")
    assert _safe_name("🚀 rocket") != ""
    assert len(_safe_name("x" * 200)) <= 40
    assert _safe_name("") != ""
    # Names with no ASCII at all must stay distinct, or one file overwrites another.
    assert _safe_name("\u0930\u093e\u091c\u0938\u094d\u0925\u093e\u0928") != _safe_name("\u0935\u094d\u092e\u0942")
