"""Rendering: text cleanup, link bases, token estimates and the output formats.

This module has no Telethon dependency on purpose, so it is cheap to import
and easy to unit-test.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Optional

from timeutil import get_tz

# Devanagari block (U+0900-U+097F). Used only for the token estimate.
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
def normalize_text(text: Optional[str]) -> str:
    """Collapse blank lines and repeated spaces without changing any words."""
    if not text:
        return ""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = "\n".join(line.rstrip() for line in cleaned.split("\n"))
    return cleaned.strip()


def ist_str(moment: datetime, tz=None) -> str:
    """Render a (naive-UTC or aware) datetime as 'YYYY-MM-DD HH:MM' in IST."""
    tz = tz or get_tz()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def build_link_base(chat_id: int, username: Optional[str]) -> str:
    """Build the per-channel link prefix.

    Public:  https://t.me/<username>/
    Private: https://t.me/c/<internal_id>/  where internal_id has no -100 prefix.
    A message link is link_base + str(message.id).
    """
    if username:
        return f"https://t.me/{username.lstrip('@')}/"

    raw = str(chat_id)
    if raw.startswith("-100"):
        internal = raw[4:]
    elif raw.startswith("-"):
        internal = raw[1:]
    else:
        internal = raw
    return f"https://t.me/c/{internal}/"


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------
def clean(obj: Any) -> Any:
    """Recursively drop empty, null and false values to save tokens."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            cleaned = clean(value)
            if _is_empty(cleaned):
                continue
            out[key] = cleaned
        return out
    if isinstance(obj, list):
        out_list = []
        for value in obj:
            cleaned = clean(value)
            if _is_empty(cleaned):
                continue
            out_list.append(cleaned)
        return out_list
    return obj


def _is_empty(value: Any) -> bool:
    if value is None or value is False or value == "":
        return True
    return bool(isinstance(value, (list, dict)) and len(value) == 0)


def dumps_compact(obj: Any) -> str:
    """The token-friendly form. ensure_ascii=False keeps Hindi as Hindi."""
    return json.dumps(clean(obj), ensure_ascii=False, separators=(",", ":"))


def dumps_pretty(obj: Any) -> str:
    """Human-readable form, for --pretty only."""
    return json.dumps(clean(obj), ensure_ascii=False, indent=2)


def build_digest(
    ai_instructions: str,
    meta: dict[str, Any],
    channels: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the top-level digest object."""
    return {
        "ai_instructions": ai_instructions,
        "meta": meta,
        "channels": channels,
    }


# --------------------------------------------------------------------------
# Alternate formats
# --------------------------------------------------------------------------
def to_jsonl(digest: dict[str, Any]) -> str:
    """First line = ai_instructions + meta, then one line per message."""
    lines = [
        dumps_compact(
            {
                "ai_instructions": digest.get("ai_instructions", ""),
                "meta": digest.get("meta", {}),
            }
        )
    ]
    for channel in digest.get("channels", []):
        prefix = {
            "channel": channel.get("name", ""),
            "link_base": channel.get("link_base", ""),
        }
        for message in channel.get("messages", []):
            row = dict(prefix)
            row.update(message)
            lines.append(dumps_compact(row))
    return "\n".join(lines) + "\n"


def to_txt(digest: dict[str, Any]) -> str:
    """Very compact: '[09:14] #1234 text' under a channel heading."""
    out: list[str] = []
    for channel in digest.get("channels", []):
        out.append(f"== {channel.get('name', '')} ({channel.get('link_base', '')}) ==")
        for message in channel.get("messages", []):
            clock = str(message.get("t", ""))[-5:]
            out.append(f"[{clock}] #{message.get('id', '')} {message.get('text', '')}".rstrip())
        out.append("")
    return "\n".join(out).strip() + "\n"


# --------------------------------------------------------------------------
# Token estimates
# --------------------------------------------------------------------------
def estimate_tokens(text: str) -> dict[str, Any]:
    """Rough token estimates. Both are labelled 'estimated' in the terminal.

    chars/4 suits Latin text; Devanagari tokenises much worse (~chars/1.5).
    """
    chars = len(text)
    devanagari_chars = len(_DEVANAGARI_RE.findall(text))
    ratio = (devanagari_chars / chars) if chars else 0.0

    latin_estimate = chars / 4 if chars else 0
    devanagari_estimate = chars / 1.5 if chars else 0
    primary = devanagari_estimate if ratio > 0.15 else latin_estimate

    return {
        "chars": chars,
        "devanagari_ratio": round(ratio, 3),
        "est_tokens_latin": round(latin_estimate),
        "est_tokens_devanagari": round(devanagari_estimate),
        "est_tokens_primary": round(primary),
    }
