"""A fake, fully offline Telethon client plus message/entity builders.

It mimics the parts of TelegramClient the app uses, and records every call so
tests can assert what the app did (and, critically, that it never called a
write/send/read-ack method).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from telethon.tl import types as t

FORBIDDEN_METHODS = [
    "send_message",
    "send_file",
    "send_read_acknowledge",
    "mark_read",
    "delete_messages",
    "edit_message",
    "forward_messages",
    "send_media",
    "send_reaction",
    "iter_participants",
    "get_participants",
]


def make_channel(
    chat_id: int,
    title: str = "Test Channel",
    username: Optional[str] = None,
    broadcast: bool = True,
    megagroup: bool = False,
) -> t.Channel:
    return t.Channel(
        id=chat_id,
        title=title,
        photo=t.ChatPhotoEmpty(),
        date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        creator=False,
        left=False,
        broadcast=broadcast,
        verified=False,
        megagroup=megagroup,
        username=username,
    )


def make_message(
    msg_id: int,
    date: datetime,
    text: Optional[str] = None,
    media: Any = None,
    entities: Optional[list] = None,
    post_author: Optional[str] = None,
    reply_to: Optional[int] = None,
    fwd: Any = None,
    edit_date: Optional[datetime] = None,
    grouped_id: Optional[int] = None,
    views: Optional[int] = None,
    sender: Any = None,
    from_id: Any = None,
) -> t.Message:
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    message = t.Message(
        id=msg_id,
        peer_id=t.PeerChannel(123),
        date=date,
        message=text,
        from_id=from_id,
    )
    message.media = media
    message.entities = entities or []
    message.post_author = post_author
    message.fwd_from = fwd
    message.grouped_id = grouped_id
    message.views = views
    if edit_date is not None:
        message.edit_date = edit_date
    if reply_to is not None:
        message.reply_to = t.MessageReplyHeader(reply_to_msg_id=reply_to)
    if sender is not None:
        message._sender = sender
    return message


def make_service(msg_id: int, date: datetime) -> t.MessageService:
    return t.MessageService(
        id=msg_id,
        peer_id=t.PeerChannel(123),
        date=date,
        action=t.MessageActionPinMessage(),
    )


def make_photo_media() -> t.MessageMediaPhoto:
    return t.MessageMediaPhoto(
        photo=t.Photo(
            id=1,
            access_hash=1,
            file_reference=b"",
            date=datetime(2020, 1, 1, tzinfo=timezone.utc),
            sizes=[],
            dc_id=1,
        )
    )


def make_document_media(filename: str = "report.pdf") -> t.MessageMediaDocument:
    doc = t.Document(
        id=1,
        access_hash=1,
        file_reference=b"",
        date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        mime_type="application/pdf",
        size=10,
        dc_id=1,
        attributes=[t.DocumentAttributeFilename(file_name=filename)],
    )
    return t.MessageMediaDocument(document=doc)


def make_webpage(url: str, title: Optional[str] = None) -> t.MessageMediaWebPage:
    page = t.WebPage(
        id=1,
        url=url,
        display_url=url,
        hash=0,
        type="article",
        site_name="Site",
        title=title,
    )
    return t.MessageMediaWebPage(webpage=page)


def make_poll(question: str, options: list[str]) -> t.MessageMediaPoll:
    answers = [t.PollAnswer(text=opt, option=bytes([i])) for i, opt in enumerate(options)]
    poll = t.Poll(id=1, question=question, answers=answers, hash=1)
    return t.MessageMediaPoll(poll=poll, results=t.PollResults())


def make_fwd(from_name: Optional[str] = None, channel_post: Optional[int] = None) -> t.MessageFwdHeader:
    return t.MessageFwdHeader(
        date=datetime(2020, 1, 1, tzinfo=timezone.utc),
        from_name=from_name,
        channel_post=channel_post,
    )


class FakeTelegramClient:
    """Records calls; serves scripted history newest-first, like the real one."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.entities: dict[Any, Any] = {}
        self.histories: dict[int, list] = {}
        self.members: set[int] = set()
        self.get_entity_errors: dict[Any, Exception] = {}
        self.iter_errors: dict[int, Exception] = {}
        self.invites: dict[str, Any] = {}          # hash -> result value
        self.invite_errors: dict[str, Exception] = {}  # hash -> exception
        self.import_result: Any = None
        self.import_errors: dict[str, Exception] = {}
        self.iter_yielded = 0
        self.iter_requests: list[Any] = []
        self.disconnected = False

    # ---- helpers ----
    def add_channel(self, channel: t.Channel, messages: Optional[list] = None) -> None:
        self.entities[channel.id] = channel
        if channel.username:
            self.entities[channel.username] = channel
        self.histories[channel.id] = messages or []

    def register_username(self, username: str, entity: Any) -> None:
        self.entities[username] = entity

    # ---- read API ----
    async def iter_messages(self, entity, offset_date=None, limit=None, reverse=False):
        entity_id = getattr(entity, "id", entity)
        self.calls.append(("iter_messages", entity_id))
        self.iter_requests.append(entity_id)
        error = self.iter_errors.get(entity_id)
        if error is not None:
            raise error
        for message in self.histories.get(entity_id, []):
            moment = message.date
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            if offset_date is not None and moment >= offset_date:
                continue
            self.iter_yielded += 1
            yield message

    async def get_entity(self, key):
        self.calls.append(("get_entity", key))
        if key in self.get_entity_errors:
            raise self.get_entity_errors[key]
        if key in self.entities:
            return self.entities[key]
        raise ValueError(f"Cannot find any entity corresponding to {key!r}")

    async def __call__(self, request):
        name = type(request).__name__
        self.calls.append(("request", name))
        if name == "CheckChatInviteRequest":
            if request.hash in self.invite_errors:
                raise self.invite_errors[request.hash]
            if request.hash in self.invites:
                return self.invites[request.hash]
            raise ValueError(f"no invite scripted for {request.hash}")
        if name == "ImportChatInviteRequest":
            if request.hash in self.import_errors:
                raise self.import_errors[request.hash]
            return self.import_result
        if name == "GetParticipantRequest":
            channel = request.channel
            if getattr(channel, "id", None) in self.members:
                return object()
            raise ValueError("not a participant")
        raise NotImplementedError(name)

    async def start(self, phone=None):  # pragma: no cover - never network
        self.calls.append(("start",))
        return self

    async def disconnect(self):  # pragma: no cover
        self.disconnected = True

    # ---- forbidden methods: present so any accidental call is loud ----
    def _forbidden(name):
        async def method(self, *args, **kwargs):
            self.calls.append((f"FORBIDDEN:{name}",))
            raise AssertionError(f"App called forbidden method {name}!")
        return method


for _name in FORBIDDEN_METHODS:
    setattr(FakeTelegramClient, _name, FakeTelegramClient._forbidden(_name))
