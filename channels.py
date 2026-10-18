"""Parse channels.txt and resolve each entry to a readable Telegram entity.

Safety rules baked in here:
  * we never join a chat unless --auto-join was given;
  * joins are rate-limited (a random 30-60s gap) and capped by --max-joins;
  * a chat that needs join-request approval is marked "pending_approval";
  * every failure is turned into a short machine-readable reason string.
"""
from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from telethon import TelegramClient
from telethon.errors import (
    ChannelBannedError,
    ChannelPrivateError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.functions.messages import CheckChatInviteRequest, ImportChatInviteRequest
from telethon.tl.types import Channel, Chat, ChatInviteAlready

from config import JOIN_WAIT_MAX_SECONDS, JOIN_WAIT_MIN_SECONDS
from formatter import build_link_base

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_HOST_RE = re.compile(r"^(?:www\.)?(?:t\.me|telegram\.me)/", re.IGNORECASE)
_INVITE_HASH_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_NUMERIC_RE = re.compile(r"^-?\d+$")
_PRIVATE_POST_RE = re.compile(r"^c/(\d+)(?:/\d+)?$")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
@dataclass
class ChannelSpec:
    """One line of channels.txt, parsed."""

    raw: str
    kind: str  # "username" | "invite" | "id" | "invalid"
    value: str
    label: Optional[str] = None
    tags: tuple[str, ...] = ()
    paused: bool = False


def parse_channel_line(line: str) -> Optional[ChannelSpec]:
    """Parse a single line. Returns None for comments and blank lines.

    Syntax (backward compatible)::

        <entry> [| label] [| tags=a,b] [| off]

    A segment that is exactly ``off`` marks the line paused; a segment
    ``tags=...`` sets tags; anything else is part of the label. Old lines
    (``entry | label``) keep working unchanged.
    """
    raw = line.strip().lstrip("\ufeff").strip()
    if not raw or raw.startswith("#"):
        return None

    segments = raw.split("|")
    body = segments[0].strip()
    label_parts: list[str] = []
    tags: list[str] = []
    paused = False
    for segment in segments[1:]:
        piece = segment.strip()
        if not piece:
            continue
        low = piece.lower()
        if low == "off":
            paused = True
        elif low.startswith("tags="):
            for tag in piece[5:].split(","):
                tag = tag.strip()
                if tag and tag not in tags:
                    tags.append(tag)
        else:
            label_parts.append(piece)
    label = "|".join(label_parts) if label_parts else None
    if not body:
        return None

    def spec(kind: str, value: str) -> ChannelSpec:
        return ChannelSpec(raw, kind, value, label, tuple(tags), paused)

    # Normalise a URL to a bare target: drop scheme, host, query and fragment.
    target = _SCHEME_RE.sub("", body)
    target = _HOST_RE.sub("", target)
    target = re.split(r"[?#]", target, maxsplit=1)[0].strip().rstrip("/")
    if target.startswith("@"):
        target = target[1:]
    if not target:
        # Non-empty line that yields no target (e.g. "https://t.me/" or "???").
        return spec("invalid", body)

    # Private invite links.
    if target.startswith("+"):
        hash_ = target[1:]
        return spec("invite", hash_) if _INVITE_HASH_RE.match(hash_) else spec("invalid", target)
    if target.lower().startswith("joinchat/"):
        hash_ = target.split("/", 1)[1]
        return spec("invite", hash_) if _INVITE_HASH_RE.match(hash_) else spec("invalid", target)

    # Private post link c/<id>/<msg> -> the channel id (-100<id>).
    private = _PRIVATE_POST_RE.match(target)
    if private:
        return spec("id", f"-100{private.group(1)}")

    # Numeric id (checked before username, since digits also match it).
    if _NUMERIC_RE.match(target):
        return spec("id", target)

    # Public username, optionally with a /<post id> suffix.
    username = target.split("/", 1)[0]
    if _USERNAME_RE.match(username):
        return spec("username", username)

    return spec("invalid", target)


def parse_channels_file(path: Path) -> list[ChannelSpec]:
    """Read channels.txt into a list of specs, preserving order.

    ``utf-8-sig`` transparently strips a UTF-8 BOM; ``splitlines`` handles CRLF.
    """
    text = Path(path).read_text(encoding="utf-8-sig")
    specs: list[ChannelSpec] = []
    for line in text.splitlines():
        spec = parse_channel_line(line)
        if spec is not None:
            specs.append(spec)
    return specs


# --------------------------------------------------------------------------
# Join bookkeeping
# --------------------------------------------------------------------------
class JoinBudgetExceeded(Exception):
    """Raised when --max-joins has been reached."""


@dataclass
class JoinState:
    """Tracks how many joins happened and enforces the wait + cap."""

    auto_join: bool = False
    max_joins: int = 5
    joins: int = 0
    wait_min: int = JOIN_WAIT_MIN_SECONDS
    wait_max: int = JOIN_WAIT_MAX_SECONDS

    async def prepare_join(self) -> None:
        """Sleep 30-60s between joins and refuse once the cap is hit."""
        if self.joins >= self.max_joins:
            raise JoinBudgetExceeded(f"max_joins={self.max_joins} reached")
        if self.joins > 0:
            await asyncio.sleep(random.uniform(self.wait_min, self.wait_max))  # nosec B311 - join jitter

    def record_join(self) -> None:
        self.joins += 1


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------
@dataclass
class Resolved:
    """Outcome of resolving one spec."""

    spec: ChannelSpec
    ok: bool
    entity: object = None
    name: str = ""
    username: Optional[str] = None
    kind: str = "channel"  # "channel" | "group"
    link_base: str = ""
    reason: str = ""
    already_member: bool = False


def _entity_to_resolved(spec: ChannelSpec, entity, *, already_member: bool = False) -> Resolved:
    real_name = (
        getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or spec.value
    )
    username = getattr(entity, "username", None)
    chat_id = getattr(entity, "id", None)

    kind = "channel"
    if isinstance(entity, Channel):
        kind = "group" if getattr(entity, "megagroup", False) else "channel"
    elif isinstance(entity, Chat):
        kind = "group"

    link_base = build_link_base(chat_id, username) if chat_id is not None else ""
    return Resolved(
        spec=spec,
        ok=True,
        entity=entity,
        name=spec.label or real_name,
        username=username,
        kind=kind,
        link_base=link_base,
        already_member=already_member,
    )


async def _is_member(client: TelegramClient, entity) -> bool:
    try:
        await client(GetParticipantRequest(channel=entity, participant="me"))
        return True
    except Exception:  # noqa: BLE001 - any error here just means "not a member"
        return False


async def _resolve_invite(
    client: TelegramClient, spec: ChannelSpec, join_state: JoinState
) -> Resolved:
    try:
        result = await client(CheckChatInviteRequest(spec.value))
    except InviteHashExpiredError:
        return Resolved(spec, False, reason="invite_expired")
    except InviteHashInvalidError:
        return Resolved(spec, False, reason="invite_invalid")

    if isinstance(result, ChatInviteAlready):
        return _entity_to_resolved(spec, result.chat, already_member=True)

    # We are not in the chat yet.
    if getattr(result, "request_needed", False):
        return Resolved(spec, False, reason="pending_approval")
    if not join_state.auto_join:
        return Resolved(spec, False, reason="not_joined")

    try:
        await join_state.prepare_join()
    except JoinBudgetExceeded:
        return Resolved(spec, False, reason="max_joins")

    try:
        updates = await client(ImportChatInviteRequest(spec.value))
    except Exception as exc:  # noqa: BLE001 - keep the run alive no matter what
        return Resolved(spec, False, reason=f"join_failed:{type(exc).__name__}")

    join_state.record_join()
    chats = getattr(updates, "chats", None) or []
    if not chats:
        return Resolved(spec, False, reason="join_no_chat")
    return _entity_to_resolved(spec, chats[0], already_member=True)


async def resolve_spec(
    client: TelegramClient, spec: ChannelSpec, join_state: JoinState
) -> Resolved:
    """Resolve one spec to a readable entity, or a Resolved(reason=...)."""
    try:
        if spec.kind == "invalid":
            return Resolved(spec, False, reason="invalid_entry")
        if spec.kind == "invite":
            return await _resolve_invite(client, spec, join_state)

        entity = (
            await client.get_entity(int(spec.value))
            if spec.kind == "id"
            else await client.get_entity(spec.value)
        )

        # Public chats are readable without joining; private ones are not.
        username = getattr(entity, "username", None)
        member = True
        if username is None:
            member = await _is_member(client, entity)
            if not member:
                return Resolved(spec, False, reason="not_joined")
        return _entity_to_resolved(spec, entity, already_member=member)

    except UsernameNotOccupiedError:
        return Resolved(spec, False, reason="not_found")
    except UsernameInvalidError:
        return Resolved(spec, False, reason="invalid_username")
    except ChannelPrivateError:
        return Resolved(spec, False, reason="private")
    except ChannelBannedError:
        return Resolved(spec, False, reason="banned")
    except ValueError:
        return Resolved(spec, False, reason="not_found")
    except Exception as exc:  # noqa: BLE001 - never let one bad line kill the run
        return Resolved(spec, False, reason=f"error:{type(exc).__name__}")
