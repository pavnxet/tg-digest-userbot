"""File-safe editing of channels.txt (section B).

Only the affected line is rewritten; comments, blank lines, order, line endings
and UTF-8 (no BOM) are preserved. Writes are atomic (temp file in the same
folder, then ``os.replace``) and a ``channels.txt.bak`` is kept before each
change. A lock file plus an "is a run active" check stop the bot and a running
scraper from overlapping.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from channels import ChannelSpec, parse_channel_line

MAX_ENTRIES = 200
MAX_INPUT_CHARS = 200
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class ChannelError(Exception):
    """A rejected channel edit; the message is safe to show the owner."""


@dataclass
class Entry:
    """One parsed entry of channels.txt."""

    index: int      # 1-based, as shown by /channels
    line_no: int    # 0-based index into the file's lines
    raw: str
    spec: ChannelSpec

    @property
    def label(self) -> Optional[str]:
        return self.spec.label

    @property
    def tags(self) -> tuple[str, ...]:
        return self.spec.tags

    @property
    def paused(self) -> bool:
        return self.spec.paused


@dataclass
class AddResult:
    ok: bool
    message: str
    duplicate_index: Optional[int] = None


def _split_line(raw: str) -> tuple[str, Optional[str], list[str], bool]:
    """Return (entry, label, tags, paused) from a raw line."""
    segments = raw.split("|")
    entry = segments[0].strip()
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
            # `a | tags=x | tags=y` keeps both -- it used to overwrite.
            for tag in piece[5:].split(","):
                tag = tag.strip()
                if tag and tag not in tags:
                    tags.append(tag)
        else:
            label_parts.append(piece)
    label = "|".join(label_parts) if label_parts else None
    return entry, label, tags, paused


def render_line(entry: str, label: Optional[str], tags: list[str], paused: bool) -> str:
    parts = [entry.strip()]
    if label:
        parts.append(label.strip())
    if tags:
        parts.append("tags=" + ",".join(tags))
    if paused:
        parts.append("off")
    return " | ".join(parts)


class ChannelStore:
    """Reads and edits channels.txt without disturbing the rest of the file."""

    def __init__(
        self,
        path: Path,
        lock_path: Path,
        *,
        max_entries: int = MAX_ENTRIES,
        ids_path: Optional[Path] = None,
        is_run_active: Optional[Callable[[], bool]] = None,
        lock_factory: Optional[Callable[[Path], Any]] = None,
    ) -> None:
        self.path = Path(path)
        self.lock_path = Path(lock_path)
        self.max_entries = int(max_entries)
        self.ids_path = Path(ids_path) if ids_path is not None else None
        self._is_run_active = is_run_active or (lambda: False)
        self._lock_factory = lock_factory
        self._ids: dict[str, int] = self._load_ids()

    # -- resolved-id cache (for de-duplicating by id, not raw string) ------
    def _load_ids(self) -> dict[str, int]:
        if self.ids_path is None:
            return {}
        try:
            import json

            data = json.loads(self.ids_path.read_text(encoding="utf-8"))
            return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def remember_id(self, key: str, resolved_id: int) -> None:
        self._ids[key] = int(resolved_id)
        if self.ids_path is not None:
            import json

            self.ids_path.parent.mkdir(parents=True, exist_ok=True)
            self.ids_path.write_text(json.dumps(self._ids), encoding="utf-8")

    # -- reading -----------------------------------------------------------
    def _read_lines(self) -> tuple[list[str], str, bool]:
        data = self.path.read_bytes() if self.path.exists() else b""
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]  # drop a BOM; we never write one back
        text = data.decode("utf-8")
        newline = "\r\n" if "\r\n" in text else "\n"
        trailing = text.endswith(("\n", "\r"))
        body = text.rstrip("\r\n")
        lines = [] if body == "" else [ln[:-1] if ln.endswith("\r") else ln for ln in body.split("\n")]
        return lines, newline, trailing

    def _write_lines(self, lines: list[str], newline: str, trailing: bool) -> None:
        text = newline.join(lines)
        if trailing and lines:
            text += newline
        # backup first
        if self.path.exists():
            shutil.copy2(self.path, self.path.with_name(self.path.name + ".bak"))
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, self.path)

    def lines(self) -> list[str]:
        return self._read_lines()[0]

    def entries(self) -> list[Entry]:
        lines = self.lines()
        out: list[Entry] = []
        index = 0
        for line_no, raw in enumerate(lines):
            spec = parse_channel_line(raw)
            if spec is None:
                continue
            index += 1
            out.append(Entry(index=index, line_no=line_no, raw=raw, spec=spec))
        return out

    def by_index(self, index: int) -> Optional[Entry]:
        for entry in self.entries():
            if entry.index == index:
                return entry
        return None

    def find(self, query: str) -> list[Entry]:
        """Match by the /channels number (preferred) or by link/name."""
        query = (query or "").strip().lstrip("#")
        if not query:
            return []
        if query.isdigit():
            entry = self.by_index(int(query))
            return [entry] if entry else []
        target = parse_channel_line(query)
        matches: list[Entry] = []
        for entry in self.entries():
            by_target = (target is not None and target.kind != "invalid"
                         and (entry.spec.kind, entry.spec.value) == (target.kind, target.value))
            by_label = entry.label is not None and entry.label.lower() == query.lower()
            if by_target or by_label:
                matches.append(entry)
        return matches

    # -- validation --------------------------------------------------------
    def validate_input(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            raise ChannelError("खाली input। / empty input.")
        if "\n" in text or "\r" in text:
            raise ChannelError("एक ही line भेजें। / one line only.")
        if len(text) > MAX_INPUT_CHARS:
            raise ChannelError(f"बहुत लंबा ({len(text)} > {MAX_INPUT_CHARS})।")
        if _CONTROL_RE.search(text):
            raise ChannelError("control characters मान्य नहीं।")
        return text

    def _key(self, spec: ChannelSpec) -> str:
        return f"{spec.kind}:{spec.value}"

    def _duplicate(self, spec: ChannelSpec, resolved_id: Optional[int]) -> Optional[Entry]:
        for entry in self.entries():
            if (entry.spec.kind, entry.spec.value) == (spec.kind, spec.value):
                return entry
        if resolved_id is not None and resolved_id in self._ids.values():
            key = next((k for k, v in self._ids.items() if v == resolved_id), None)
            if key is not None:
                for entry in self.entries():
                    if self._key(entry.spec) == key:
                        return entry
        return None

    # -- editing -----------------------------------------------------------
    def _guard(self) -> None:
        if self._is_run_active():
            raise ChannelError("scrape चल रहा है; रुकने के बाद बदलें। / run in progress.")
        if self._lock_factory is not None:
            lock = self._lock_factory(self.lock_path)
            if not lock.acquire(os.getpid()):
                raise ChannelError("channels.txt किसी और process ने lock किया है।")
            self._release: Any = lock
        else:
            self._release = None

    def _unguard(self) -> None:
        lock = getattr(self, "_release", None)
        if lock is not None:
            with contextlib.suppress(Exception):
                lock.release()
        self._release = None

    def add(
        self,
        text: str,
        *,
        label: Optional[str] = None,
        tags: Optional[list[str]] = None,
        resolved_id: Optional[int] = None,
    ) -> AddResult:
        text = self.validate_input(text)
        spec = parse_channel_line(text)
        if spec is None or spec.kind == "invalid":
            raise ChannelError("यह entry पहचान नहीं पाई। / could not parse this entry.")
        self._guard()
        try:
            duplicate = self._duplicate(spec, resolved_id)
            if duplicate is not None:
                return AddResult(False, f"पहले से मौजूद (#{duplicate.index})।", duplicate_index=duplicate.index)
            if len(self.entries()) >= self.max_entries:
                raise ChannelError(f"सूची भर गई (max {self.max_entries})।")
            lines, newline, _ = self._read_lines()
            # `spec.raw` still carries the '| label' part; write the entry alone
            # so the label is not duplicated.
            entry_text = text.split("|", 1)[0].strip()
            lines.append(render_line(entry_text, label or spec.label,
                                     list(tags or spec.tags), spec.paused))
            self._write_lines(lines, newline, True)
            return AddResult(True, "जोड़ दिया। / added.")
        finally:
            self._unguard()

    def remove(self, index: int) -> None:
        self._guard()
        try:
            entry = self.by_index(index)
            if entry is None:
                raise ChannelError(f"#{index} नहीं मिला।")
            lines, newline, trailing = self._read_lines()
            del lines[entry.line_no]
            self._write_lines(lines, newline, trailing)
        finally:
            self._unguard()

    def set_paused(self, index: int, paused: bool) -> None:
        self._mutate(index, lambda e, l, t, p: (e, l, t, paused))

    def add_tag(self, index: int, tag: str) -> None:
        tag = (tag or "").strip()
        if not tag or _CONTROL_RE.search(tag) or "|" in tag:
            raise ChannelError("tag मान्य नहीं।")

        def change(entry, label, tags, paused):
            if tag not in tags:
                tags = [*tags, tag]
            return entry, label, tags, paused

        self._mutate(index, change)

    def remove_tag(self, index: int, tag: str) -> None:
        tag = (tag or "").strip()

        def change(entry, label, tags, paused):
            return entry, label, [t for t in tags if t != tag], paused

        self._mutate(index, change)

    def _mutate(self, index: int, change) -> None:
        self._guard()
        try:
            entry = self.by_index(index)
            if entry is None:
                raise ChannelError(f"#{index} नहीं मिला।")
            lines, newline, trailing = self._read_lines()
            e, label, tags, paused = _split_line(lines[entry.line_no])
            e, label, tags, paused = change(e, label, tags, paused)
            lines[entry.line_no] = render_line(e, label, tags, paused)
            self._write_lines(lines, newline, trailing)
        finally:
            self._unguard()
