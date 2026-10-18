"""Split a digest into size-bounded parts, each a standalone valid JSON object.

Splitting happens only at channel boundaries, or at message boundaries when a
single channel is itself larger than the budget. Every part carries its own
ai_instructions and meta (with meta.part = "X/Y"), so any part can be pasted
into an AI on its own.
"""
from __future__ import annotations

from typing import Any

from formatter import dumps_compact

# Leave a little headroom so the final "X/Y" part label never tips a part over.
_SAFETY_MARGIN = 64
_MIN_BUDGET = 1000


def _serialized_size(obj: Any) -> int:
    return len(dumps_compact(obj))


def _overhead(ai_instructions: str, meta: dict[str, Any]) -> int:
    """Bytes used by everything except the channel array itself."""
    skeleton = {
        "ai_instructions": ai_instructions,
        "meta": {**meta, "part": "00/00"},
        "channels": [],
    }
    return _serialized_size(skeleton)


def _split_channel(channel: dict[str, Any], budget: int) -> list[dict[str, Any]]:
    """Split one oversized channel at message boundaries.

    Each returned fragment repeats the channel header (name, username, type,
    link_base) so it is self-describing.
    """
    header = {k: v for k, v in channel.items() if k != "messages"}
    messages = channel.get("messages", [])
    empty_size = _serialized_size({**header, "count": 0, "messages": []})

    fragments: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_size = empty_size

    for message in messages:
        message_size = _serialized_size(message) + 1  # +1 for the comma
        if current and current_size + message_size > budget:
            fragments.append({**header, "count": len(current), "messages": current})
            current = []
            current_size = empty_size
        current.append(message)
        current_size += message_size

    if current:
        fragments.append({**header, "count": len(current), "messages": current})
    return fragments


def split_digest(
    ai_instructions: str,
    meta: dict[str, Any],
    channels: list[dict[str, Any]],
    max_chars: int,
) -> list[dict[str, Any]]:
    """Return one or more complete digest objects, each under `max_chars`."""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")

    budget = max(_MIN_BUDGET, max_chars - _overhead(ai_instructions, meta) - _SAFETY_MARGIN)

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_size = 0

    for channel in channels:
        channel_size = _serialized_size(channel) + 1
        if channel_size > budget:
            # Flush whatever we have, then emit this channel as its own parts.
            if current:
                groups.append(current)
                current, current_size = [], 0
            for fragment in _split_channel(channel, budget):
                groups.append([fragment])
            continue

        if current and current_size + channel_size > budget:
            groups.append(current)
            current, current_size = [], 0
        current.append(channel)
        current_size += channel_size

    if current:
        groups.append(current)
    if not groups:
        groups = [[]]

    total = len(groups)
    parts: list[dict[str, Any]] = []
    for index, group in enumerate(groups, start=1):
        part_meta = {**meta, "part": f"{index}/{total}"}
        parts.append(
            {
                "ai_instructions": ai_instructions,
                "meta": part_meta,
                "channels": group,
            }
        )
    return parts
