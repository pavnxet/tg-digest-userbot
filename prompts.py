"""Summarisation prompts: list, validate and load them from ``prompts/``.

No AI service is ever called here. This module only stores, selects and returns
prompt text. Each file starts with one title line ``# <name> - <description>``;
that line is used for listings and stripped when the prompt is embedded.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_log = logging.getLogger("tgdigest.prompts")

PROMPTS_DIR: Path = Path(__file__).resolve().parent / "prompts"
NAME_RE = re.compile(r"^[a-z0-9_-]{1,40}$")
MAX_PROMPT_CHARS = 8000
DEFAULT_NAME = "default"
_TITLE_RE = re.compile(r"^#\s+(?P<name>[a-z0-9_-]+)\s+-\s+(?P<desc>.+)$")


class PromptError(Exception):
    """A rejected prompt name or an unreadable / oversized prompt file."""


@dataclass(frozen=True)
class PromptInfo:
    name: str
    title: str
    description: str


def _validate_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise PromptError(
            f"invalid prompt name {name!r} (allowed: a-z, 0-9, _ , - ; 1-40 characters)"
        )
    return name


def _resolve(name: str, directory: Optional[Path]) -> Path:
    """Return the resolved path of prompts/<name>.md, or raise PromptError."""
    root = Path(directory) if directory is not None else PROMPTS_DIR
    _validate_name(name)
    try:
        root_resolved = root.resolve(strict=True)
    except OSError as exc:
        raise PromptError(f"prompts folder not found: {root}") from exc
    path = root / f"{name}.md"
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PromptError(f"prompt not found: {name}") from exc
    if resolved.parent != root_resolved:
        raise PromptError(f"prompt {name!r} escapes the prompts folder")
    if not resolved.is_file():
        raise PromptError(f"prompt not found: {name}")
    return resolved


def _strip_title(text: str) -> str:
    lines = text.splitlines()
    if lines and _TITLE_RE.match(lines[0].strip()):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def list_prompts(directory: Optional[Path] = None) -> list[PromptInfo]:
    """Return name, title and description for each prompts/*.md, sorted by name."""
    root = Path(directory) if directory is not None else PROMPTS_DIR
    if not root.is_dir():
        return []
    infos: list[PromptInfo] = []
    for path in sorted(root.glob("*.md")):
        name = path.stem
        if not NAME_RE.match(name):
            continue
        try:
            first = path.read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, IndexError, UnicodeDecodeError):
            continue
        match = _TITLE_RE.match(first)
        if match and match.group("name") == name:
            infos.append(PromptInfo(name=name, title=first, description=match.group("desc").strip()))
        else:
            infos.append(PromptInfo(name=name, title=f"# {name}", description=""))
    return sorted(infos, key=lambda info: info.name)


def available_names(directory: Optional[Path] = None) -> list[str]:
    return [info.name for info in list_prompts(directory)]


def load_prompt(name: str, directory: Optional[Path] = None) -> str:
    """Return the prompt text without its first title line.

    Rejects names that are not ``[a-z0-9_-]{1,40}``, files outside prompts/, and
    files larger than MAX_PROMPT_CHARS.
    """
    path = _resolve(name, directory)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PromptError(f"cannot read prompt {name}: {exc}") from exc
    if len(raw) > MAX_PROMPT_CHARS:
        raise PromptError(f"prompt {name} is too large ({len(raw)} > {MAX_PROMPT_CHARS} characters)")
    body = _strip_title(raw)
    if not body:
        raise PromptError(f"prompt {name} is empty")
    return body


def default_prompt(fallback: str, directory: Optional[Path] = None) -> str:
    """The embedded default prompt, or `fallback` if prompts/default.md is unreadable."""
    try:
        return load_prompt(DEFAULT_NAME, directory)
    except PromptError as exc:
        _log.warning("could not load the default prompt (%s); using the built-in text", exc)
        return fallback
