"""Phase 5: property-based and fuzz tests (hypothesis)."""
import json
from datetime import datetime, timezone

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from channels import parse_channel_line
from fake_client import make_message
from formatter import dumps_compact, estimate_tokens, normalize_text
from scraper import extract_links
from splitter import split_digest

# Exclude lone surrogates: real Telegram text is valid UTF-8 and cannot contain them.
safe_text = st.text(alphabet=st.characters(blacklist_categories=("Cs",)))
# Control characters are always \u-escaped by JSON, so exclude them for the
# "no escapes" assertion (they are handled separately).
printable_text = st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")))
settings_fast = settings(max_examples=60, deadline=None,
                         suppress_health_check=[HealthCheck.function_scoped_fixture])


# --------------------------------------------------------------------------
# Text handling
# --------------------------------------------------------------------------
@given(safe_text)
@settings_fast
def test_normalize_never_leaves_triple_newlines_or_double_spaces(text):
    out = normalize_text(text)
    assert "\n\n\n" not in out
    assert "  " not in out
    assert out == out.strip()


@given(printable_text)
@settings_fast
def test_compact_json_round_trips(text):
    payload = {"text": text}
    out = dumps_compact(payload)
    assert json.loads(out) == (payload if text else {})
    assert "\\u" not in out


@given(st.text(alphabet=st.characters(min_codepoint=0x0900, max_codepoint=0x097F)))
@settings_fast
def test_devanagari_estimate_always_higher(text):
    est = estimate_tokens(text)
    if est["chars"]:
        assert est["est_tokens_devanagari"] >= est["est_tokens_latin"]


@given(safe_text)
@settings_fast
def test_parser_never_raises(line):
    parse_channel_line(line)  # must not raise for any input


# --------------------------------------------------------------------------
# Hyperlink UTF-16 correctness
# --------------------------------------------------------------------------
@given(safe_text, safe_text)
@settings_fast
def test_hyperlink_offset_always_matches_target(prefix, suffix):
    target = "TARGET"
    text = prefix + target + suffix
    offset = len(prefix.encode("utf-16-le")) // 2
    length = len(target.encode("utf-16-le")) // 2
    from telethon.tl import types as t

    entity = t.MessageEntityTextUrl(offset=offset, length=length, url="https://u")
    msg = make_message(1, datetime(2026, 10, 18, tzinfo=timezone.utc), text=text, entities=[entity])
    assert extract_links(msg) == ["https://u"]


# --------------------------------------------------------------------------
# Splitter invariant
# --------------------------------------------------------------------------
def _build_channels(specs):
    channels = []
    counter = 0
    for index, (count, size) in enumerate(specs):
        messages = []
        for _ in range(count):
            messages.append({"id": counter, "t": "2026-10-18 09:00", "text": "x" * size})
            counter += 1
        channels.append({
            "name": f"C{index}", "username": "ch", "type": "channel",
            "link_base": "https://t.me/ch/", "count": count, "messages": messages,
        })
    return channels


def _meta():
    return {"generated_at": "2026-10-18 18:50 IST", "timezone": "Asia/Kolkata",
            "from": "2026-10-18 00:00", "to": "2026-10-18 18:50",
            "channels_ok": 0, "messages_total": 0, "part": "1/1"}


@given(
    st.lists(st.tuples(st.integers(1, 15), st.integers(1, 200)), min_size=0, max_size=5),
    st.integers(min_value=1500, max_value=20000),
)
@settings_fast
def test_splitter_reconstructs_original_without_loss(specs, max_chars):
    channels = _build_channels(specs)
    original = sorted(m["id"] for ch in channels for m in ch["messages"])
    parts = split_digest("instr", _meta(), channels, max_chars)

    collected = []
    for index, part in enumerate(parts, start=1):
        json.loads(dumps_compact(part))  # each part is valid JSON on its own
        assert part["ai_instructions"] == "instr"
        assert part["meta"]["part"] == f"{index}/{len(parts)}"
        collected.extend(m["id"] for ch in part["channels"] for m in ch["messages"])

    assert sorted(collected) == original
    assert len(collected) == len(set(collected))
