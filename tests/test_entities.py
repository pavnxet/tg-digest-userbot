"""Hyperlink extraction from Hindi/emoji text.

Telegram entity offsets are in UTF-16 code units, so a naive Python slice
(`text[offset:offset+length]`) silently returns the wrong substring whenever
the text contains emoji or other astral characters. We use Telethon's
get_entities_text, which does the UTF-16 arithmetic correctly.
"""
from datetime import datetime, timezone

from telethon.tl.types import Message, MessageEntityTextUrl, PeerUser

from scraper import extract_links


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _make_message(text: str, target: str, url: str) -> Message:
    prefix = text[: text.index(target)]
    entity = MessageEntityTextUrl(
        offset=_utf16_len(prefix),
        length=_utf16_len(target),
        url=url,
    )
    return Message(
        id=1,
        peer_id=PeerUser(1),
        date=datetime.now(timezone.utc),
        message=text,
        entities=[entity],
    )


def test_hidden_link_in_hindi_text():
    text = "नमस्ते दोस्तों! यह लिंक देखें: click here"
    message = _make_message(text, "click here", "https://example.com/hi")
    assert extract_links(message) == ["https://example.com/hi"]


def test_hidden_link_with_emoji_before_it():
    text = "🎉🎉 बड़ी खबर! आज की ताज़ा खबर पढ़ें here"
    message = _make_message(text, "here", "https://example.com/news")
    assert extract_links(message) == ["https://example.com/news"]


def test_utf16_offset_differs_from_python_index():
    # This is the bug the code avoids: with emoji, the UTF-16 offset and the
    # Python string index disagree, so manual slicing would grab the wrong text.
    text = "🎉🎉 hello world"
    target = "world"
    python_index = text.index(target)
    utf16_offset = _utf16_len(text[:python_index])
    assert python_index != utf16_offset
    # Telethon still returns the correct URL because it respects UTF-16.
    message = _make_message(text, target, "https://example.com/ok")
    assert extract_links(message) == ["https://example.com/ok"]


def test_multiple_links_are_deduplicated_and_ordered():
    text = "a 🚀 b 🚀 c"
    first = MessageEntityTextUrl(offset=0, length=1, url="https://one.example")
    # offsets are UTF-16 based; build them explicitly here.
    prefix_b = text[: text.index("b")]
    second = MessageEntityTextUrl(
        offset=_utf16_len(prefix_b), length=1, url="https://two.example"
    )
    message = Message(
        id=2,
        peer_id=PeerUser(1),
        date=datetime.now(timezone.utc),
        message=text,
        entities=[first, second],
    )
    assert extract_links(message) == ["https://one.example", "https://two.example"]
