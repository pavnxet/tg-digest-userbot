"""Channel-management commands (section B), behind the shared owner guard.

All of these run through ``BotApp.handle`` / ``handle_callback``; the panel only
registers commands and confirmation actions. Resolution uses read-only user
client calls only (get_entity / CheckChatInviteRequest) -- never a join.

Inline buttons carry a short **token**, never a live index: an old button can
then never act on a different channel after the list has changed.
"""
from __future__ import annotations

import logging
import secrets
import time
from typing import Any, Optional

from telethon.tl.custom import Button

from channel_store import ChannelError
from channels import parse_channel_line

_log = logging.getLogger("tgdigest.bot.channels")

PAGE_SIZE = 20
PICK_TTL_SECONDS = 120.0
MAX_LIST_TOKENS = 32


class ChannelPanel:
    """Adds /channels /add /remove /pause /resume /tag /untag /groups."""

    def __init__(self, app: Any) -> None:
        self.app = app
        self.store = app.channels_store
        self._tags: dict[str, Optional[str]] = {}      # token -> tag filter
        self._picks: dict[str, tuple[str, float]] = {}  # token -> (raw line, expiry)
        app.commands.update({
            "/channels": self.cmd_channels,
            "/add": self.cmd_add,
            "/remove": self.cmd_remove,
            "/pause": self.cmd_pause,
            "/resume": self.cmd_resume,
            "/tag": self.cmd_tag,
            "/untag": self.cmd_untag,
            "/groups": self.cmd_groups,
        })
        app.register_confirmation("remove", self._do_remove)
        app.register_confirmation("add_anyway", self._do_add_anyway)
        app.register_callback_prefix(b"chlist:", self._cb_page)
        app.register_callback_prefix(b"rm:", self._cb_pick)

    # -- helpers -----------------------------------------------------------
    def _arg(self, event: Any) -> str:
        return (getattr(event, "text", "") or "").partition(" ")[2].strip()

    def _filtered(self, tag: Optional[str]) -> list:
        entries = self.store.entries()
        if tag:
            entries = [e for e in entries if tag in e.tags]
        return entries

    def _token_for(self, tag: Optional[str]) -> str:
        """A short, stable token for a tag filter (callback data is capped at 64B)."""
        for token, existing in self._tags.items():
            if existing == tag:
                return token
        token = secrets.token_urlsafe(5)
        self._tags[token] = tag
        while len(self._tags) > MAX_LIST_TOKENS:
            self._tags.pop(next(iter(self._tags)))
        return token

    def _page_view(self, tag: Optional[str], page: int) -> tuple[str, list]:
        entries = self._filtered(tag)
        total_pages = max(1, (len(entries) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        chunk = entries[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
        head = f"📋 Channels ({len(entries)}" + (f", tag={tag}" if tag else "") + f") — page {page + 1}/{total_pages}\n"
        lines = []
        for entry in chunk:
            marks = []
            if entry.paused:
                marks.append("⏸")
            if entry.spec.kind == "invalid":
                marks.append("⚠️")
            tags = f" [{' '.join('#' + t for t in entry.tags)}]" if entry.tags else ""
            label = f" {entry.label}" if entry.label else ""
            lines.append(f"{entry.index}. {entry.spec.value}{label}{tags} {' '.join(marks)}".rstrip())
        text = head + "\n".join(lines) if lines else head + "(खाली)"

        # The page number travels in the button, so each message pages itself.
        token = self._token_for(tag)
        row = []
        if page > 0:
            row.append(Button.inline("⬅️", f"chlist:{token}:{page - 1}".encode()))
        if page < total_pages - 1:
            row.append(Button.inline("➡️", f"chlist:{token}:{page + 1}".encode()))
        return text, ([row] if row else [])

    async def _resolve_add(self, target_text: str) -> Optional[dict]:
        spec = parse_channel_line(target_text)
        if spec is None or spec.kind == "invalid":
            return None
        info = {"spec": spec, "title": None, "member": None, "id": None, "unverified": False}
        try:
            await self.app.user.connect()
        except Exception:  # noqa: BLE001
            return info
        try:
            if spec.kind == "invite":
                result = await self.app.user.check_invite(spec.value)
                chat = getattr(result, "chat", None)
                info["title"] = getattr(result, "title", None) or getattr(chat, "title", None)
                info["member"] = chat is not None
                info["id"] = getattr(chat, "id", None)
            else:
                key = int(spec.value) if spec.kind == "id" else spec.value
                entity = await self.app.user.get_entity(key)
                info["title"] = getattr(entity, "title", None) or getattr(entity, "first_name", None)
                # get_entity succeeding does NOT mean we are a member: any public
                # channel resolves. Leave membership unknown rather than claim it.
                info["member"] = None
                info["id"] = getattr(entity, "id", None)
        except Exception:  # noqa: BLE001 - unverifiable (e.g. a numeric id not in cache)
            info["unverified"] = True
        finally:
            await self.app.user.disconnect()
        return info

    # -- commands ----------------------------------------------------------
    async def cmd_channels(self, event: Any) -> None:
        tag = self._arg(event) or None
        text, buttons = self._page_view(tag, 0)
        await self.app._respond(event, text, buttons=buttons or None)

    async def cmd_groups(self, event: Any) -> None:
        counts: dict[str, int] = {}
        for entry in self.store.entries():
            for tag in entry.tags:
                counts[tag] = counts.get(tag, 0) + 1
        if not counts:
            await self.app._respond(event, "कोई tag group नहीं। /tag <n> <tag> से बनाएँ।")
            return
        lines = [f"#{tag} — {n}" for tag, n in sorted(counts.items())]
        await self.app._respond(event, "🏷 Tag groups:\n" + "\n".join(lines))

    async def cmd_add(self, event: Any) -> None:
        arg = self._arg(event)
        if not arg:
            await self.app._respond(event, "उपयोग: /add <link | @name | id> [| label]")
            return
        try:
            text = self.store.validate_input(arg)
        except ChannelError as exc:
            await self.app._respond(event, f"❌ {exc}")
            return
        info = await self._resolve_add(text)
        if info is None:
            await self.app._respond(event, "❌ यह entry पहचान नहीं पाई।")
            return
        if info["unverified"]:
            spec = parse_channel_line(text)
            known_id = int(spec.value) if spec is not None and spec.kind == "id" else None
            await self.app.ask_confirmation(
                event, "यह id session cache से verify नहीं हो सका। फिर भी जोड़ें?",
                "add_anyway", {"text": text, "resolved_id": known_id},
                confirm_label="फिर भी जोड़ें / Add anyway",
            )
            return
        try:
            result = self.store.add(text, resolved_id=info["id"])
        except ChannelError as exc:
            await self.app._respond(event, f"❌ {exc}")
            return
        if not result.ok:
            await self.app._respond(event, f"• {result.message}")
            return
        title = info["title"] or "?"
        if info["member"] is False:
            note = "\n⚠️ आप member नहीं हैं — scraper इसे skip करेगा।"
        elif info["member"] is None:
            note = "\nℹ️ सदस्यता की जाँच नहीं हो सकी — पहला run बताएगा।"
        else:
            note = ""
        await self.app._respond(event, f"✅ जोड़ा: {title}{note}")

    async def _do_add_anyway(self, event: Any, payload: dict) -> None:
        try:
            result = self.store.add(payload["text"], resolved_id=payload.get("resolved_id"))
        except ChannelError as exc:
            await self.app._respond(event, f"❌ {exc}")
            return
        await self.app._respond(event, "✅ जोड़ दिया (unverified)।" if result.ok else f"• {result.message}")

    async def cmd_remove(self, event: Any) -> None:
        arg = self._arg(event)
        if not arg:
            await self.app._respond(event, "उपयोग: /remove <#>  (जैसे /remove 3)")
            return
        matches = self.store.find(arg)
        if not matches:
            await self.app._respond(event, "❌ कुछ नहीं मिला। /channels से नंबर देखें।")
            return
        if len(matches) > 1:
            buttons = []
            for match in matches:
                token = secrets.token_urlsafe(6)
                self._picks[token] = (match.raw, time.time() + PICK_TTL_SECONDS)
                buttons.append([Button.inline(f"#{match.index} {match.spec.value}", f"rm:{token}".encode())])
            await self.app._respond(event, "कौन सा हटाएँ? / Which one?", buttons=buttons)
            return
        entry = matches[0]
        await self.app.ask_confirmation(
            event, f"#{entry.index} {entry.spec.value} हटाएँ? / Remove?", "remove", {"raw": entry.raw},
        )

    async def _cb_pick(self, event: Any, rest: bytes) -> None:
        """A '#<n>' button from an ambiguous /remove."""
        token = rest.decode("utf-8", "replace")
        item = self._picks.pop(token, None)
        if item is None or time.time() > item[1]:
            await self.app._respond(event, "यह button पुराना है। /remove फिर से भेजें।")
            return
        raw, _expiry = item
        await self.app.ask_confirmation(
            event, f"{raw} हटाएँ? / Remove?", "remove", {"raw": raw},
        )

    async def _do_remove(self, event: Any, payload: dict) -> None:
        raw = payload.get("raw")
        index = payload.get("index")
        try:
            if raw is not None:
                # Re-resolve now: the index may have shifted since the button.
                matches = [e for e in self.store.entries() if e.raw == raw]
                if not matches:
                    await self.app._respond(event, "❌ वह channel अब list में नहीं है।")
                    return
                index = matches[0].index
            if index is None:
                await self.app._respond(event, "❌ हटाने के लिए कुछ नहीं मिला।")
                return
            self.store.remove(int(index))
        except (ChannelError, TypeError) as exc:
            await self.app._respond(event, f"❌ {exc}")
            return
        await self.app._respond(event, "🗑 हटा दिया। / Removed.")

    async def _apply_index(self, event: Any, verb: str, index_str: str, fn) -> None:
        index_str = (index_str or "").lstrip("#")
        if not index_str.isdigit():
            await self.app._respond(event, f"उपयोग: /{verb} <#>")
            return
        try:
            fn(int(index_str))
        except ChannelError as exc:
            await self.app._respond(event, f"❌ {exc}")
            return
        await self.app._respond(event, "✅ हो गया। / done.")

    async def cmd_pause(self, event: Any) -> None:
        await self._apply_index(event, "pause", self._arg(event), lambda i: self.store.set_paused(i, True))

    async def cmd_resume(self, event: Any) -> None:
        await self._apply_index(event, "resume", self._arg(event), lambda i: self.store.set_paused(i, False))

    async def cmd_tag(self, event: Any) -> None:
        parts = self._arg(event).split()
        if len(parts) < 2:
            await self.app._respond(event, "उपयोग: /tag <#> <tag>")
            return
        await self._apply_index(event, "tag", parts[0], lambda i: self.store.add_tag(i, parts[1]))

    async def cmd_untag(self, event: Any) -> None:
        parts = self._arg(event).split()
        if len(parts) < 2:
            await self.app._respond(event, "उपयोग: /untag <#> <tag>")
            return
        await self._apply_index(event, "untag", parts[0], lambda i: self.store.remove_tag(i, parts[1]))

    # -- pagination -------------------------------------------------------
    async def _cb_page(self, event: Any, rest: bytes) -> None:
        """Page a /channels message in place; the token carries its tag filter."""
        token, _, page_text = rest.decode("utf-8", "replace").partition(":")
        if token not in self._tags or not page_text.isdigit():
            await self.app._respond(event, "यह list पुरानी है। /channels फिर से भेजें।")
            return
        text, buttons = self._page_view(self._tags[token], int(page_text))
        await self.app._edit(event, text, buttons=buttons or None)
