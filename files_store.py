"""Output files: listing, retention and local search (section F).

Retention and /clean only ever touch ``digest_*`` files directly inside the
configured output folder. A path is never taken from chat input, and a symlink
whose target escapes the output folder is skipped.
"""
from __future__ import annotations

import json
import logging
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from timeutil import get_tz

_log = logging.getLogger("tgdigest.bot.files")

SNIPPET_CHARS = 200
FIND_LIMIT = 10


@dataclass
class FileInfo:
    name: str
    path: Path
    size: int
    mtime: float
    messages: int


def _message_count(path: Path) -> int:
    try:
        if path.suffix == ".json":
            return int(json.loads(path.read_text(encoding="utf-8")).get("meta", {}).get("messages_total", 0))
        if path.suffix == ".jsonl":
            return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
    except (OSError, ValueError, TypeError):
        pass
    return 0


def _safe_in_dir(path: Path, output_dir: Path) -> bool:
    """True if `path` is a real file inside `output_dir` (no escaping symlink)."""
    try:
        resolved = path.resolve(strict=True)
        root = output_dir.resolve(strict=True)
    except OSError:
        return False
    return resolved.parent == root and resolved.name.startswith("digest_")


def list_outputs(output_dir: Path) -> list[FileInfo]:
    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        return []
    infos = []
    for path in output_dir.glob("digest_*"):
        if not _safe_in_dir(path, output_dir):
            continue
        stat = path.stat()
        infos.append(FileInfo(path.name, path, stat.st_size, stat.st_mtime, _message_count(path)))
    infos.sort(key=lambda i: i.mtime, reverse=True)
    return infos


def files_to_clean(output_dir: Path, days: Optional[int], now: Optional[float] = None) -> list[FileInfo]:
    """The files /clean would delete. ``days=None`` means every digest file."""
    output_dir = Path(output_dir)
    if days is None:
        return list_outputs(output_dir)
    cutoff = (now if now is not None else datetime.now(get_tz()).timestamp()) - days * 86400
    return [info for info in list_outputs(output_dir) if info.mtime < cutoff]


def delete_files(infos: list[FileInfo]) -> tuple[list[str], int]:
    """Delete the given files. Returns (deleted names, bytes freed)."""
    names: list[str] = []
    freed = 0
    for info in infos:
        try:
            info.path.unlink()
        except OSError:
            _log.warning("could not delete an output file")
            continue
        names.append(info.name)
        freed += info.size
    return names, freed


def clean_older_than(output_dir: Path, days: int, now: Optional[float] = None) -> list[str]:
    """Delete digest_* files older than `days`. Returns the deleted names."""
    names, _freed = delete_files(files_to_clean(output_dir, days, now))
    return names


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def find(
    keyword: str,
    output_dir: Path,
    window_hours: Optional[float] = None,
    limit: int = FIND_LIMIT,
    now: Optional[datetime] = None,
) -> list[dict]:
    """Search saved digests locally (no Telegram call)."""
    needle = _normalise(keyword.strip())
    if not needle:
        return []
    tz = get_tz()
    now = now or datetime.now(tz)
    since = now - timedelta(hours=window_hours) if window_hours else None
    matches: list[dict] = []
    for info in list_outputs(output_dir):
        try:
            raw_text = info.path.read_text(encoding="utf-8")
        except OSError:
            continue
        documents: list[dict] = []
        if info.path.suffix == ".json":
            try:
                documents = [json.loads(raw_text)]
            except ValueError:
                continue
        elif info.path.suffix == ".jsonl":
            # JSONL is one record per *message* ({"channel", "id", "link_base",
            # "text"}), plus a meta line -- so json.loads on the file always failed.
            for line in raw_text.splitlines():
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                text = record.get("text")
                if not isinstance(text, str) or needle not in _normalise(text):
                    continue
                when = record.get("t") or record.get("date")
                if since is not None and when:
                    try:
                        stamp = datetime.fromisoformat(str(when))
                        if stamp.tzinfo is None:
                            stamp = stamp.replace(tzinfo=tz)
                        if stamp < since:
                            continue
                    except ValueError:
                        pass
                link_base = record.get("link_base") or ""
                matches.append({
                    "channel": record.get("channel") or "?",
                    "time": when,
                    "snippet": " ".join(text.split())[:SNIPPET_CHARS],
                    "link": f"{link_base}{record.get('id', '')}" if link_base else "",
                })
                if len(matches) >= limit:
                    return matches
            continue
        elif info.path.suffix == ".txt":
            for line in raw_text.splitlines():
                if needle not in _normalise(line):
                    continue
                matches.append({
                    "channel": "?",
                    "time": None,
                    "snippet": " ".join(line.split())[:SNIPPET_CHARS],
                    "link": "",
                })
                if len(matches) >= limit:
                    return matches
            continue
        else:
            continue
        for data in documents:
            for channel in data.get("channels", []):
                link_base = channel.get("link_base", "")
                name = (channel.get("name") or channel.get("label")
                        or channel.get("username") or "?")
                for message in channel.get("messages", []):
                    text = message.get("text") or ""
                    if needle not in _normalise(text):
                        continue
                    when = message.get("t")
                    if since is not None and when:
                        try:
                            stamp = datetime.fromisoformat(when)
                            if stamp.tzinfo is None:
                                stamp = stamp.replace(tzinfo=tz)
                            if stamp < since:
                                continue
                        except ValueError:
                            pass
                    snippet = " ".join(text.split())[:SNIPPET_CHARS]
                    matches.append({
                        "channel": name,
                        "time": when,
                        "snippet": snippet,
                        "link": f"{link_base}{message.get('id', '')}" if link_base else "",
                    })
                    if len(matches) >= limit:
                        return matches
    return matches
