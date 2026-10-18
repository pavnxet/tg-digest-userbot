"""The /help text and the bot command menu stay in sync with the commands."""
import asyncio

from bot_handlers import COMMAND_MENU, HELP_TEXT
from fake_bot_client import FakeEvent, build_app

OWNER = 555

# Commands the bot actually registers (from every panel).
REGISTERED = {
    "start", "help", "login", "login_phone", "status", "logout", "cancel",
    "channels", "add", "remove", "pause", "resume", "tag", "untag", "groups",
    "run", "stop", "preview", "last", "schedule", "settings", "set",
    "files", "clean", "find", "health", "panic",
}


def test_help_mentions_every_command_group():
    for token in ["/channels", "/add", "/run", "/stop", "/schedule", "/settings", "/files", "/health", "/panic"]:
        assert token in HELP_TEXT, f"{token} missing from HELP_TEXT"


def test_command_menu_matches_registered_commands():
    names = [name for name, _ in COMMAND_MENU]
    assert set(names) == REGISTERED
    assert len(names) == len(set(names)), "duplicate command names"


def test_command_menu_respects_telegram_limits():
    for name, description in COMMAND_MENU:
        assert 1 <= len(name) <= 32, name
        assert not name.startswith("/"), name
        assert name.islower(), name
        assert 3 <= len(description) <= 256, name


def test_registered_handlers_cover_the_menu(tmp_path):
    script = tmp_path / "fake_main.py"
    script.write_text("pass\n", encoding="utf-8")
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n", run_script=script)
    registered = {cmd.lstrip("/") for cmd in app.commands}
    missing = REGISTERED - registered
    assert not missing, f"menu lists commands with no handler: {missing}"


def test_start_replies_with_the_help_text(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/start", OWNER)))
    assert bot.sent[-1][1] == HELP_TEXT
    asyncio.run(app.handle(FakeEvent(bot, "/help", OWNER)))
    assert "/panic" in bot.sent[-1][1]
