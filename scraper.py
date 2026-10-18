"""Scraping: read messages from one channel and normalise them.

Read-only by construction -- only `iter_messages` and `get_messages` are used.
Nothing here ever sends, reacts, or marks anything as read.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from telethon import TelegramClient
from telethon.errors import (
    ChannelBannedError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    FloodWaitError,
)
from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeAudio,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    DocumentAttributeVideo,
    MessageEntityTextUrl,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaPoll,
    MessageMediaWebPage,
    MessageService,
)

from formatter import ist_str, normalize_text

ProgressCallback = Optional[Callable[[int], None]]

_log = logging.getLogger("tgdigest.scraper")


@dataclass
class ScrapeOptions:
    """Everything scrape_channel needs beyond the channel and the window."""

    tz: Any
    max_per_channel: int = 5000
    with_views: bool = False
    dedupe: bool = False
    min_chars: int = 0
    max_flood_wait: int = 900


# --------------------------------------------------------------------------
# Small helpers (kept pure so they are easy to test)
# --------------------------------------------------------------------------
def to_utc(moment: Optional[datetime]) -> Optional[datetime]:
    """Telethon may hand back naive-UTC datetimes; normalise to aware UTC."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def media_label(message: Any) -> Optional[str]:
    """A short media tag such as 'photo', 'voice' or 'document:file.pdf'."""
    media = getattr(message, "media", None)
    if media is None or isinstance(media, (MessageMediaWebPage, MessageMediaPoll)):
        return None
    if isinstance(media, MessageMediaPhoto):
        return "photo"
    if isinstance(media, MessageMediaDocument):
        document = media.document
        attributes = getattr(document, "attributes", []) or []
        for attribute in attributes:
            if isinstance(attribute, DocumentAttributeSticker):
                return "sticker"
        for attribute in attributes:
            if isinstance(attribute, DocumentAttributeVideo):
                return "video"
        for attribute in attributes:
            if isinstance(attribute, DocumentAttributeAudio):
                return "voice" if getattr(attribute, "voice", False) else "audio"
        for attribute in attributes:
            if isinstance(attribute, DocumentAttributeAnimated):
                return "gif"
        for attribute in attributes:
            if isinstance(attribute, DocumentAttributeFilename):
                return f"document:{attribute.file_name}"
        return "document"
    return None


def extract_links(message: Any) -> list[str]:
    """Real URLs behind hidden hyperlinks, plus any link-preview URL.

    Telethon's get_entities_text handles the UTF-16 offset/length arithmetic
    for us, so Hindi and emoji text is sliced correctly.
    """
    urls: list[str] = []
    try:
        # get_entities_text yields (entity, visible_text); the URL lives on
        # the entity itself.
        for entity, _visible_text in message.get_entities_text(MessageEntityTextUrl):
            url = getattr(entity, "url", None)
            if url and url not in urls:
                urls.append(url)
    except Exception:  # noqa: BLE001 - a malformed entity must not break extraction
        _log.debug("could not read hyperlink entities from a message")

    media = getattr(message, "media", None)
    if isinstance(media, MessageMediaWebPage):
        preview_url = getattr(media.webpage, "url", None)
        if preview_url and preview_url not in urls:
            urls.append(preview_url)
    return urls


def preview_title(message: Any) -> Optional[str]:
    media = getattr(message, "media", None)
    if isinstance(media, MessageMediaWebPage):
        return getattr(media.webpage, "title", None) or None
    return None


def extract_author(message: Any) -> Optional[str]:
    """post_author for channels; the sender's name for groups."""
    if getattr(message, "post_author", None):
        return message.post_author
    sender = getattr(message, "sender", None)
    if sender is not None:
        title = getattr(sender, "title", None)
        if title:
            return title
        parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
        name = " ".join(part for part in parts if part)
        return name or getattr(sender, "username", None)
    return None


def _as_text(value: Any) -> Optional[str]:
    """Poll question/answer fields are TypeTextWithEntities.

    That union is either a plain ``str`` or a ``TextWithEntities`` object whose
    ``.text`` holds the string, so support both.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    text = getattr(value, "text", None)
    return text or None


def extract_poll(message: Any) -> Optional[dict[str, Any]]:
    # message.poll is the MessageMediaPoll *wrapper*; the real Poll is inside it.
    media = getattr(message, "media", None)
    if not isinstance(media, MessageMediaPoll):
        return None
    poll = getattr(media, "poll", None)
    if poll is None:
        return None

    question = _as_text(getattr(poll, "question", None))
    options = [_as_text(a.text) for a in (getattr(poll, "answers", None) or [])]
    options = [option for option in options if option]
    if not question and not options:
        return None
    return {"question": question, "options": options}


def forward_source(message: Any) -> Optional[str]:
    fwd = getattr(message, "fwd_from", None)
    if fwd is None:
        return None
    if getattr(fwd, "from_name", None):
        return fwd.from_name
    if getattr(fwd, "post_author", None):
        return fwd.post_author
    if getattr(fwd, "channel_post", None):
        return f"channel:{fwd.channel_post}"
    return None


# --------------------------------------------------------------------------
# Message -> dict
# --------------------------------------------------------------------------
def normalize_message(message: Any, opts: ScrapeOptions) -> Optional[dict[str, Any]]:
    """Convert one Telethon message into the output dict, or None to skip it."""
    if isinstance(message, MessageService):
        return None

    text = normalize_text(getattr(message, "message", None) or "")
    poll = extract_poll(message)
    if not text and not poll:
        return None  # nothing readable (e.g. a bare sticker or an empty album item)

    moment = to_utc(getattr(message, "date", None))
    if moment is None:
        return None
    result: dict[str, Any] = {"id": message.id, "t": ist_str(moment, opts.tz)}
    if text:
        result["text"] = text

    author = extract_author(message)
    if author:
        result["author"] = author

    reply_to = getattr(message, "reply_to_msg_id", None)
    if reply_to:
        result["reply_to"] = reply_to

    fwd = forward_source(message)
    if fwd:
        result["fwd"] = fwd

    if getattr(message, "edit_date", None):
        result["edited"] = True

    media = media_label(message)
    if media:
        result["media"] = media

    if poll:
        result["poll"] = poll

    links = extract_links(message)
    if links:
        result["links"] = links

    title = preview_title(message)
    if title:
        result["preview"] = title

    if opts.with_views and getattr(message, "views", None):
        result["views"] = message.views

    grouped_id = getattr(message, "grouped_id", None)
    if grouped_id:
        result["_gid"] = grouped_id

    return result


# --------------------------------------------------------------------------
# Post-processing: albums, dedupe, min length
# --------------------------------------------------------------------------
def _combine_media(labels: list[str]) -> str:
    unique = sorted(set(labels))
    if len(unique) == 1:
        return f"{unique[0]} x{len(labels)}"
    return f"{'+'.join(unique)} x{len(labels)}"


def merge_albums(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge consecutive messages sharing a grouped_id into one entry."""
    merged: list[dict[str, Any]] = []
    index = 0
    total = len(messages)

    while index < total:
        message = messages[index]
        grouped_id = message.get("_gid")
        if grouped_id is None:
            clean_message = dict(message)
            clean_message.pop("_gid", None)
            merged.append(clean_message)
            index += 1
            continue

        group = [message]
        next_index = index + 1
        while next_index < total and messages[next_index].get("_gid") == grouped_id:
            group.append(messages[next_index])
            next_index += 1
        merged.append(_merge_group(group))
        index = next_index
    return merged


def _merge_group(group: list[dict[str, Any]]) -> dict[str, Any]:
    base = dict(group[0])
    base.pop("_gid", None)

    captions = [item.get("text", "") for item in group if item.get("text")]
    joined = normalize_text("\n".join(captions))
    if joined:
        base["text"] = joined
    else:
        base.pop("text", None)

    labels = [item["media"] for item in group if item.get("media")]
    if labels:
        base["media"] = _combine_media(labels)

    links: list[str] = []
    for item in group:
        for url in item.get("links", []):
            if url not in links:
                links.append(url)
    if links:
        base["links"] = links
    else:
        base.pop("links", None)

    return base


def dedupe_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop later messages whose text is identical to an earlier one."""
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for message in messages:
        key = (message.get("text") or "").strip()
        if key:
            if key in seen:
                continue
            seen.add(key)
        kept.append(message)
    return kept


def filter_min_chars(messages: list[dict[str, Any]], minimum: int) -> list[dict[str, Any]]:
    """Drop text-only messages shorter than `minimum` characters."""
    if minimum <= 0:
        return messages
    kept: list[dict[str, Any]] = []
    for message in messages:
        if message.get("media") or message.get("poll"):
            kept.append(message)
            continue
        if len(message.get("text", "")) >= minimum:
            kept.append(message)
    return kept


# --------------------------------------------------------------------------
# The main read
# --------------------------------------------------------------------------
async def _collect(
    client: TelegramClient,
    resolved: Any,
    window: Any,
    opts: ScrapeOptions,
    progress: ProgressCallback,
) -> tuple[list[dict[str, Any]], bool]:
    collected: list[dict[str, Any]] = []
    scanned = 0
    truncated = False

    # Newest -> oldest; stop the moment we pass the window start.
    async for message in client.iter_messages(resolved.entity, offset_date=window.until):
        moment = to_utc(getattr(message, "date", None))
        if moment is None:
            continue
        if not window.contains(moment):  # single source of truth: [since, until)
            if moment < window.since:
                break
            continue
        scanned += 1
        if opts.max_per_channel and scanned > opts.max_per_channel:
            truncated = True  # there is at least one more message we are not reading
            break
        if progress is not None:
            progress(1)
        normalized = normalize_message(message, opts)
        if normalized is not None:
            collected.append(normalized)

    # Albums are adjacent in iteration order -> merge before sorting.
    collected = merge_albums(collected)
    collected.sort(key=lambda item: (item["t"], item["id"]))  # oldest -> newest

    if opts.dedupe:
        collected = dedupe_messages(collected)
    if opts.min_chars:
        collected = filter_min_chars(collected, opts.min_chars)
    return collected, truncated


async def scrape_channel(
    client: TelegramClient,
    resolved: Any,
    window: Any,
    opts: ScrapeOptions,
    progress: ProgressCallback = None,
) -> tuple[Optional[list[dict[str, Any]]], Optional[str], bool]:
    """Read one channel. Returns (messages, reason, truncated).

    reason is None on success. Any error becomes a short reason string so a
    single bad channel is recorded in meta.skipped and the run continues.
    """
    for attempt in range(3):
        try:
            collected, truncated = await _collect(client, resolved, window, opts, progress)
            return collected, None, truncated

        except FloodWaitError as exc:
            seconds = exc.seconds
            if seconds <= 60:
                await asyncio.sleep(seconds + 1)  # Telethon's own auto-sleep
                continue
            if seconds <= opts.max_flood_wait:
                await asyncio.sleep(seconds + 2)
                continue
            return None, "flood_wait", False

        except (ChannelPrivateError, ChannelBannedError, ChatAdminRequiredError):
            return None, "no_access", False

        except (ConnectionError, TimeoutError, OSError) as exc:
            if attempt == 2:
                return None, f"network:{type(exc).__name__}", False
            await asyncio.sleep(2 ** attempt)  # 1s, 2s, then give up
            continue

        except Exception as exc:  # noqa: BLE001 - one bad channel must never kill the run
            # NB: KeyboardInterrupt / SystemExit / asyncio.CancelledError are
            # BaseException, not Exception, so they still propagate and stop the
            # run cleanly (checkpoint kept, state.json untouched).
            return None, f"unexpected_error:{type(exc).__name__}", False

    return None, "flood_wait", False


async def count_channel(
    client: TelegramClient,
    resolved: Any,
    window: Any,
    opts: ScrapeOptions,
) -> int:
    """Lightweight message count for --dry-run (no extraction, no storage)."""
    count = 0
    try:
        async for message in client.iter_messages(resolved.entity, offset_date=window.until):
            moment = to_utc(getattr(message, "date", None))
            if moment is None:
                continue
            if not window.contains(moment):
                if moment < window.since:
                    break
                continue
            count += 1
            if opts.max_per_channel and count >= opts.max_per_channel:
                break
    except Exception:  # noqa: BLE001 - dry-run count is best-effort
        return count
    return count
