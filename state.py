"""Small JSON state helpers: last-run timestamp and the resume run id."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from timeutil import get_tz


def load_state(path: Path) -> dict[str, Any]:
    """Read state.json, returning {} if it is missing or corrupt."""
    file = Path(path)
    if not file.exists():
        return {}
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path: Path, data: dict[str, Any]) -> None:
    """Write state.json atomically-ish (small file, plain write is fine)."""
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_last_run(path: Path) -> Optional[datetime]:
    """Return the last successful run time as a UTC datetime, or None."""
    raw = load_state(path).get("last_run_utc")
    if not raw:
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def set_last_run(path: Path, moment: datetime, tz=None) -> None:
    """Record a successful run. Call this ONLY after the output was written."""
    tz = tz or get_tz()
    moment_utc = moment.astimezone(timezone.utc)
    data = load_state(path)
    data["last_run_utc"] = moment_utc.isoformat()
    data["last_run_ist"] = moment_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M:%S")
    save_state(path, data)


# --------------------------------------------------------------------------
# Run id, used to name the checkpoint file so --resume can find it again.
# --------------------------------------------------------------------------
def get_run_id(cache_dir: Path) -> Optional[str]:
    file = Path(cache_dir) / "last_run_id.txt"
    if not file.exists():
        return None
    value = file.read_text(encoding="utf-8").strip()
    return value or None


def set_run_id(cache_dir: Path, run_id: str) -> None:
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    (Path(cache_dir) / "last_run_id.txt").write_text(run_id, encoding="utf-8")
