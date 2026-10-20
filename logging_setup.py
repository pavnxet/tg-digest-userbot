"""Logging: everything to logs/run.log (rotated at 5 MB), warnings+ to the terminal.

Every line carries the current run id, so a manual run and a scheduled run are
easy to tell apart in the log.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)-7s [%(run_id)s] %(name)s: %(message)s"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUPS = 3

_run_id = "-"


def set_run_id(run_id: "str | None") -> None:
    """Tag subsequent log lines with this run's id (call once a run starts)."""
    global _run_id
    _run_id = run_id or "-"


class _RunIdFilter(logging.Filter):
    """Adds `run_id` to every record so the format string can use it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id
        return True


def setup_logging(log_path: Path, *, verbose: bool = False) -> logging.Logger:
    """Configure the root logger and return this project's logger."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    file_handler = RotatingFileHandler(
        log_path, maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    file_handler.addFilter(_RunIdFilter())
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    stream_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    stream_handler.addFilter(_RunIdFilter())
    root.addHandler(stream_handler)

    # Telethon is chatty at INFO; keep its noise out of the log file.
    logging.getLogger("telethon").setLevel(logging.WARNING)

    return logging.getLogger("tgdigest")
