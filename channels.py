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
from dataclasses import dataclass, field
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
from telethon.tl.types import Channel, Chat, ChatInvite, ChatInviteAlready

from config import JOIN_WAIT_MAX_SECONDS, JOIN_WAIT_MIN_SECONDS
from formatter import build_link_base

_INVITE_RE = re.compile(r"(?:t\.me/)?(?:joinchat/|\+)([A-Za-z0-9_-]+)")
_USERNAME_RE = re.compile(r"^(?:https?://t\.me/|@)([A-Za-z0-9_]{4,})/?$", re.IGNORECASE)
_NUMERIC_RE = re.compile(r"^-?\d+$")


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
@dataclass
class ChannelSpec:
    """One line of channels.txt, parsed."""

    raw: str
    kind: str  # "username" | "invite" | "id"
    value: str
    label: Optional[str] = None


def parse_channel_line(line: str) -> Optional[ChannelSpec]:
    """Parse a single line. Returns None for comments and blank lines."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    label: Optional[str] = None
    if "|" in line:
        line, label = line.split("|", 1)
        line = line.strip()
        label = label.strip() or None

    if not line:
        return None

    invite = _INVITE_RE.search(line)
    if invite and ("+" in line or "joinchat" in line.lower()):
        return ChannelSpec(line, "invite", invite.group(1), label)

    username = _USERNAME_RE.match(line)
    if username:
        return ChannelSpec(line, "username", username.group(1), label)

    if _NUMERIC_RE.match(line):
        return ChannelSpec(line, "id", line, label)

    # Last resort: treat a bare word as a username.
    return ChannelSpec(line, "username", line.lstrip("@"), label)


def parse_channels_file(path: Path) -> list[ChannelSpec]:
    """Read channels.txt into a list of specs, preserving order."""
    text = Path(path).read_text(encoding="utf-8")
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
            await asyncio.sleep(random.uniform(self.wait_min, self.wait_max))

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
    except Exception:
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
    except Exception as exc:  # keep the run alive no matter what
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
    except Exception as exc:  # never let one bad line kill the run
        return Resolved(spec, False, reason=f"error:{type(exc).__name__}")
