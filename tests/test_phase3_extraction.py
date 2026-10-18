"""Phase 3: message extraction (normalize_message and helpers)."""
from datetime import datetime, timedelta, timezone

import pytest
from telethon.tl import types as t

from fake_client import (
    make_document_media,
    make_fwd,
    make_message,
    make_photo_media,
    make_poll,
    make_service,
    make_webpage,
)
from scraper import (
    ScrapeOptions,
    extract_links,
    extract_poll,
    forward_source,
    media_label,
    normalize_message,
)
from timeutil import get_tz

TZ = get_tz()
OPTS = ScrapeOptions(tz=TZ)
BASE = datetime(2026, 10, 18, 3, 44, tzinfo=timezone.utc)  # 09:14 IST


def _n(msg):
    return normalize_message(msg, OPTS)


# --------------------------------------------------------------------------
# Basic shapes
# --------------------------------------------------------------------------
def test_plain_text_message():
    out = _n(make_message(1, BASE, text="hello world"))
    assert out["id"] == 1
    assert out["t"] == "2026-10-18 09:14"
    assert out["text"] == "hello world"


def test_caption_only_media_keeps_text_and_media():
    out = _n(make_message(2, BASE, text="a caption", media=make_photo_media()))
    assert out["text"] == "a caption" and out["media"] == "photo"


def test_media_with_no_text_is_skipped():
    assert _n(make_message(3, BASE, media=make_photo_media())) is None


def test_service_message_is_skipped():
    assert _n(make_service(4, BASE)) is None


def test_empty_message_is_skipped():
    assert _n(make_message(5, BASE)) is None


def test_whitespace_only_message_is_skipped():
    assert _n(make_message(6, BASE, text="   \n\n   ")) is None


def test_edited_flag():
    out = _n(make_message(7, BASE, text="x", edit_date=BASE + timedelta(minutes=5)))
    assert out["edited"] is True


def test_reply_to():
    out = _n(make_message(8, BASE, text="x", reply_to=7))
    assert out["reply_to"] == 7


def test_post_author_present_and_absent():
    assert _n(make_message(9, BASE, text="x", post_author="Editor"))["author"] == "Editor"
    assert "author" not in _n(make_message(10, BASE, text="x"))


def test_group_author_from_sender():
    user = t.User(id=2, first_name="Ram", last_name="Kumar")
    out = _n(make_message(11, BASE, text="x", sender=user))
    assert out["author"] == "Ram Kumar"


def test_document_media_label():
    out = _n(make_message(12, BASE, text="doc", media=make_document_media("report.pdf")))
    assert out["media"] == "document:report.pdf"


def test_poll_extraction():
    out = _n(make_message(13, BASE, media=make_poll("Which?", ["A", "B", "C"])))
    assert out["poll"]["question"] == "Which?"
    assert out["poll"]["options"] == ["A", "B", "C"]


def test_poll_only_message_is_not_dropped():
    out = _n(make_message(14, BASE, media=make_poll("Q?", ["A"])))
    assert out is not None and "poll" in out


def test_poll_with_text_with_entities_shapes():
    # Poll question/answer are TypeTextWithEntities: real Telegram may send
    # TextWithEntities objects instead of plain strings.
    media = t.MessageMediaPoll(
        poll=t.Poll(
            id=1,
            question=t.TextWithEntities(text="Which one?", entities=[]),
            answers=[t.PollAnswer(text=t.TextWithEntities(text="Yes", entities=[]), option=b"\x00")],
            hash=1,
        ),
        results=t.PollResults(),
    )
    out = _n(make_message(15, BASE, media=media))
    assert out["poll"]["question"] == "Which one?"
    assert out["poll"]["options"] == ["Yes"]


def test_link_preview_url_and_title():
    out = _n(make_message(14, BASE, text="read this", media=make_webpage("https://ex.com/a", "Title")))
    assert out["links"] == ["https://ex.com/a"]
    assert out["preview"] == "Title"


def test_forwarded_from_channel_name():
    out = _n(make_message(15, BASE, text="f", fwd=make_fwd(from_name="Source Chan")))
    assert out["fwd"] == "Source Chan"


def test_views_only_with_flag():
    msg = make_message(16, BASE, text="x", views=100)
    assert "views" not in _n(msg)
    assert normalize_message(msg, ScrapeOptions(tz=TZ, with_views=True))["views"] == 100


# --------------------------------------------------------------------------
# Hidden hyperlinks: UTF-16 offsets on tricky text
# --------------------------------------------------------------------------
def _msg_with_link(text, target, url):
    prefix = text[: text.index(target)]
    offset = len(prefix.encode("utf-16-le")) // 2
    length = len(target.encode("utf-16-le")) // 2
    entity = t.MessageEntityTextUrl(offset=offset, length=length, url=url)
    return make_message(99, BASE, text=text, entities=[entity])


@pytest.mark.parametrize(
    "text,target",
    [
        ("नमस्ते दोस्तों यहाँ क्लिक करें here", "here"),
        ("🎉🎉 खबर! click me", "click me"),
        ("👨‍👩‍👧‍👦 परिवार के लिए यहाँ देखें link", "link"),
        ("café\u0301 combining नमस्ते click", "click"),
        ("مرحبا بالعالم क्लिक here", "here"),
    ],
)
def test_hidden_link_utf16_various_scripts(text, target):
    msg = _msg_with_link(text, target, "https://example.com/x")
    assert extract_links(msg) == ["https://example.com/x"]


def test_utf16_offset_differs_from_python_index():
    text = "🎉🎉 hello world"
    target = "world"
    assert text.index(target) != len(text[: text.index(target)].encode("utf-16-le")) // 2
    assert extract_links(_msg_with_link(text, target, "https://ok")) == ["https://ok"]


def test_multiple_links_deduped_in_order():
    text = "a 🚀 b 🚀 c"
    first = t.MessageEntityTextUrl(offset=0, length=1, url="https://one")
    second = t.MessageEntityTextUrl(
        offset=len(text[: text.index("b")].encode("utf-16-le")) // 2, length=1, url="https://two"
    )
    third = t.MessageEntityTextUrl(offset=0, length=1, url="https://one")  # duplicate
    msg = make_message(1, BASE, text=text, entities=[first, second, third])
    assert extract_links(msg) == ["https://one", "https://two"]


# --------------------------------------------------------------------------
# Long / weird text
# --------------------------------------------------------------------------
def test_very_long_message_is_kept_intact():
    text = "क" * 4096
    out = _n(make_message(20, BASE, text=text))
    assert len(out["text"]) == 4096


def test_rtl_text_preserved():
    out = _n(make_message(21, BASE, text="مرحبا بالعالم"))
    assert out["text"] == "مرحبا بالعالم"


def test_internal_blank_lines_collapsed():
    out = _n(make_message(22, BASE, text="a\n\n\n\n\nb"))
    assert out["text"] == "a\n\nb"


def test_media_label_none_for_webpage_and_poll():
    assert media_label(make_message(1, BASE, media=make_webpage("https://x"))) is None
    assert media_label(make_message(2, BASE, media=make_poll("q", ["a"]))) is None


def test_forward_source_hidden_user_with_only_from_name():
    assert forward_source(make_message(1, BASE, fwd=make_fwd(from_name="Hidden"))) == "Hidden"


def test_extract_poll_none_when_no_poll():
    assert extract_poll(make_message(1, BASE, text="x")) is None
