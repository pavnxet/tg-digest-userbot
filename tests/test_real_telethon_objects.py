"""Verify the fakes against REAL Telethon objects.

The BUG-12 poll bug existed because a fake and reality disagreed about what
``Message.poll`` returns. The builders in ``fake_client`` already construct real
Telethon types, but this file pins that down: every type the fake imitates is
built for real and pushed through the real extraction code. It also records the
places where the *fake client's behaviour* could still diverge from Telethon's.
"""
import asyncio
import inspect
from datetime import datetime, timedelta, timezone

from telethon.errors import ChannelPrivateError, FloodWaitError
from telethon.tl import types as t
from telethon.tl.custom import message as custom_message

from fake_client import (
    FakeTelegramClient,
    make_channel,
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
    extract_author,
    extract_links,
    forward_source,
    media_label,
    normalize_message,
)
from timeutil import get_tz

TZ = get_tz()
OPTS = ScrapeOptions(tz=TZ)
BASE = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# The builders really do return Telethon types
# --------------------------------------------------------------------------
def test_builders_return_real_telethon_types():
    assert isinstance(make_message(1, BASE, text="x"), t.Message)
    assert isinstance(make_service(2, BASE), t.MessageService)
    assert isinstance(make_photo_media(), t.MessageMediaPhoto)
    assert isinstance(make_document_media(), t.MessageMediaDocument)
    assert isinstance(make_webpage("https://x"), t.MessageMediaWebPage)
    assert isinstance(make_poll("q", ["a"]), t.MessageMediaPoll)
    assert isinstance(make_fwd(from_name="s"), t.MessageFwdHeader)
    assert isinstance(make_channel(1, "c", username="c"), t.Channel)
    # Message subclasses the custom (patched) Message, so its helpers are real.
    assert isinstance(make_message(1, BASE, text="x"), custom_message.Message)


def test_real_message_poll_property_returns_media_wrapper():
    # The root cause of BUG-12, asserted against the installed Telethon.
    msg = make_message(1, BASE, media=make_poll("Q?", ["A", "B"]))
    assert isinstance(msg.poll, t.MessageMediaPoll)
    assert not hasattr(msg.poll, "question")
    assert msg.media.poll.question == "Q?"


def test_real_sender_property_is_synchronous_no_network():
    # extract_author reads message.sender; make sure that is a sync property
    # returning the cached sender, not a network call per message.
    assert not inspect.iscoroutinefunction(custom_message.Message.sender.fget)
    user = t.User(id=2, first_name="Ram")
    msg = make_message(1, BASE, text="x", sender=user)
    assert extract_author(msg) == "Ram"


# --------------------------------------------------------------------------
# Real objects through the real extraction code
# --------------------------------------------------------------------------
def test_real_poll_plain_string():
    out = normalize_message(make_message(1, BASE, media=make_poll("Which?", ["A", "B"])), OPTS)
    assert out["poll"] == {"question": "Which?", "options": ["A", "B"]}


def test_real_poll_text_with_entities():
    media = t.MessageMediaPoll(
        poll=t.Poll(
            id=1,
            question=t.TextWithEntities(text="Which?", entities=[]),
            answers=[t.PollAnswer(text=t.TextWithEntities(text="Yes", entities=[]), option=b"\x00")],
            hash=1,
        ),
        results=t.PollResults(),
    )
    out = normalize_message(make_message(1, BASE, media=media), OPTS)
    assert out["poll"]["question"] == "Which?" and out["poll"]["options"] == ["Yes"]


def test_real_media_labels():
    assert media_label(make_message(1, BASE, media=make_photo_media())) == "photo"
    assert media_label(make_message(2, BASE, media=make_document_media("a.pdf"))) == "document:a.pdf"
    assert media_label(make_message(3, BASE, media=make_webpage("https://x"))) is None
    assert media_label(make_message(4, BASE, media=make_poll("q", ["a"]))) is None


def test_real_webpage_preview():
    out = normalize_message(make_message(1, BASE, text="read", media=make_webpage("https://ex.com/a", "Title")), OPTS)
    assert out["links"] == ["https://ex.com/a"] and out["preview"] == "Title"


def test_real_hidden_link_utf16_hindi_emoji():
    text = "🎉 खबर! यहाँ देखें here"
    prefix = text[: text.index("here")]
    entity = t.MessageEntityTextUrl(
        offset=len(prefix.encode("utf-16-le")) // 2,
        length=len("here".encode("utf-16-le")) // 2,
        url="https://example.com/x",
    )
    msg = make_message(1, BASE, text=text, entities=[entity])
    assert extract_links(msg) == ["https://example.com/x"]


def test_real_forward_sources():
    assert forward_source(make_message(1, BASE, fwd=make_fwd(from_name="Chan"))) == "Chan"
    assert forward_source(make_message(2, BASE, fwd=make_fwd(channel_post=99))) == "channel:99"
    assert forward_source(make_message(3, BASE)) is None


def test_real_channel_entity_fields():
    ch = make_channel(1234567890, "News", username="news")
    assert ch.id == 1234567890 and ch.username == "news" and ch.title == "News"


def test_real_error_types_are_caught_types():
    # The scraper catches these exact classes; make sure we build the real ones.
    assert isinstance(ChannelPrivateError(request=None), ChannelPrivateError)
    assert isinstance(FloodWaitError(request=None, capture=120), FloodWaitError)


# --------------------------------------------------------------------------
# Fake-client behaviour vs Telethon's documented behaviour
# --------------------------------------------------------------------------
def test_fake_iter_messages_matches_offset_date_semantics():
    # Telethon: offset_date returns messages OLDER than that date (exclusive).
    fake = FakeTelegramClient()
    ch = make_channel(1, "c", username="c")
    fake.add_channel(ch, [make_message(3, BASE, text="at"), make_message(2, BASE - timedelta(minutes=1), text="old")])
    seen = []

    async def collect():
        async for m in fake.iter_messages(ch, offset_date=BASE):
            seen.append(m.id)

    asyncio.run(collect())
    assert seen == [2]  # the message exactly at offset_date is excluded
