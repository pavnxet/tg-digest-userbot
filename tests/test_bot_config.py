"""Bot settings loading."""
import pytest

from config import load_bot_settings

ENV_KEYS = ("API_ID", "API_HASH", "BOT_TOKEN", "OWNER_ID", "PHONE")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def _env(tmp_path, **values):
    path = tmp_path / ".env"
    path.write_text("\n".join(f"{k}={v}" for k, v in values.items()), encoding="utf-8")
    return path


def test_missing_bot_token_is_friendly(tmp_path):
    env = _env(tmp_path, API_ID=1, API_HASH="h")
    with pytest.raises(RuntimeError) as exc:
        load_bot_settings(env)
    assert "BOT_TOKEN" in str(exc.value)


def test_bad_api_id(tmp_path):
    env = _env(tmp_path, API_ID="abc", API_HASH="h", BOT_TOKEN="t")
    with pytest.raises(RuntimeError):
        load_bot_settings(env)


def test_bad_owner_id(tmp_path):
    env = _env(tmp_path, API_ID=1, API_HASH="h", BOT_TOKEN="t", OWNER_ID="not-an-int")
    with pytest.raises(RuntimeError):
        load_bot_settings(env)


def test_full_settings(tmp_path):
    env = _env(tmp_path, API_ID=42, API_HASH="hash", BOT_TOKEN="tok", OWNER_ID="777", PHONE="+91")
    settings = load_bot_settings(env)
    assert settings.api_id == 42
    assert settings.bot_token == "tok"
    assert settings.owner_id == 777
    assert settings.phone == "+91"


def test_owner_id_is_optional(tmp_path):
    env = _env(tmp_path, API_ID=42, API_HASH="hash", BOT_TOKEN="tok")
    settings = load_bot_settings(env)
    assert settings.owner_id is None
