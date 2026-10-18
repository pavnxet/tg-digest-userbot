"""Logging: everything to logs/run.log, warnings+ to the terminal."""
from __future__ import annotations

import logging
from pathlib import Path

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(log_path: Path, *, verbose: bool = False) -> logging.Logger:
    """Configure the root logger and return this project's logger."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(logging.DEBUG if verbose else logging.WARNING)
    stream_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    root.addHandler(stream_handler)

    # Telethon is chatty at INFO; keep its noise out of the log file.
    logging.getLogger("telethon").setLevel(logging.WARNING)

    return logging.getLogger("tgdigest")
