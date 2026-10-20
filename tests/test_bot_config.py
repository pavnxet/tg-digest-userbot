"""Bot settings loading."""
import pytest

from config import load_bot_settings

ENV_KEYS = ("API_ID", "API_HASH", "BOT_TOKEN", "OWNER_ID", "PHONE")

HASH = "0123456789abcdef0123456789abcdef"              # 32 hex chars
TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
PHONE = "+919812345678"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _env(tmp_path, **values):
    path = tmp_path / ".env"
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    return path


def test_missing_bot_token_is_friendly(tmp_path):
    env = _env(tmp_path, API_ID=1, API_HASH=HASH)
    with pytest.raises(RuntimeError) as exc:
        load_bot_settings(env)
    assert "BOT_TOKEN" in str(exc.value)


def test_bad_api_id(tmp_path):
    env = _env(tmp_path, API_ID="abc", API_HASH=HASH, BOT_TOKEN=TOKEN)
    with pytest.raises(RuntimeError):
        load_bot_settings(env)


def test_bad_api_hash(tmp_path):
    env = _env(tmp_path, API_ID=1, API_HASH="not-a-32-char-hex", BOT_TOKEN=TOKEN)
    with pytest.raises(RuntimeError) as exc:
        load_bot_settings(env)
    assert "API_HASH" in str(exc.value)


def test_bad_bot_token(tmp_path):
    env = _env(tmp_path, API_ID=1, API_HASH=HASH, BOT_TOKEN="nope")
    with pytest.raises(RuntimeError) as exc:
        load_bot_settings(env)
    assert "BOT_TOKEN" in str(exc.value)


def test_bad_owner_id(tmp_path):
    env = _env(tmp_path, API_ID=1, API_HASH=HASH, BOT_TOKEN=TOKEN, OWNER_ID="not-an-int")
    with pytest.raises(RuntimeError):
        load_bot_settings(env)


def test_full_settings(tmp_path):
    env = _env(tmp_path, API_ID=42, API_HASH=HASH, BOT_TOKEN=TOKEN, OWNER_ID="777", PHONE=PHONE)
    settings = load_bot_settings(env)
    assert settings.api_id == 42
    assert settings.bot_token == TOKEN
    assert settings.owner_id == 777
    assert settings.phone == PHONE


def test_owner_id_is_optional(tmp_path):
    env = _env(tmp_path, API_ID=42, API_HASH=HASH, BOT_TOKEN=TOKEN)
    settings = load_bot_settings(env)
    assert settings.owner_id is None
