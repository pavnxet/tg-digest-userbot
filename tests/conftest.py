"""Shared fixtures. Also puts the project root and this tests/ dir on sys.path."""
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent
for path in (str(ROOT), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from fake_client import FakeTelegramClient  # noqa: E402


@pytest.fixture
def fake_client() -> FakeTelegramClient:
    return FakeTelegramClient()


@pytest.fixture
def schema():
    import json

    path = TESTS_DIR / "schemas" / "digest.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def project(tmp_path, monkeypatch):
    """Redirect all runtime paths into tmp_path and neutralise real network use."""
    import config
    import main

    out = tmp_path / "output"
    cache = tmp_path / ".cache"
    logs = tmp_path / "logs"
    for directory in (out, cache, logs):
        directory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main, "OUTPUT_DIR", out)
    monkeypatch.setattr(main, "CACHE_DIR", cache)
    monkeypatch.setattr(main, "LOG_DIR", logs)
    monkeypatch.setattr(main, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(main, "CHANNELS_PATH", tmp_path / "channels.txt")
    monkeypatch.setattr(config, "ensure_directories", lambda: None)
    return {"tmp": tmp_path, "out": out, "cache": cache, "logs": logs,
            "state": tmp_path / "state.json", "channels": tmp_path / "channels.txt"}


def run_main(monkeypatch, project, fake, argv, channels_text=None):
    """Write channels.txt (unless channels_text is None), stub client/login, run."""
    import asyncio
    from types import SimpleNamespace

    import main

    if channels_text is not None:
        project["channels"].write_text(channels_text, encoding="utf-8")
    monkeypatch.setattr(main, "build_client", lambda creds, session: fake)

    async def _noop_login(*args, **kwargs):
        return None

    monkeypatch.setattr(main, "login", _noop_login)
    monkeypatch.setattr(
        main, "load_credentials",
        lambda: SimpleNamespace(api_id=1, api_hash="hash", phone="+10000000000"),
    )
    args = main.build_parser().parse_args(argv)
    return asyncio.run(main.run(args))
