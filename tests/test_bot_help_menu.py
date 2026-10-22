"""The /help text and the bot command menu stay in sync with the commands."""
import asyncio

from bot_handlers import COMMAND_MENU, HELP_TEXT
from fake_bot_client import FakeEvent, build_app

OWNER = 555

# Commands the bot actually registers (from every panel).
REGISTERED = {
    "start", "help", "login", "login_phone", "status", "logout", "cancel",
    "channels", "add", "remove", "pause", "resume", "tag", "untag", "groups",
    "run", "history", "stop", "preview", "last", "schedule", "settings", "set",
    "prompts", "prompt",
    "files", "clean", "find", "health", "panic",
}


def test_help_mentions_every_command_group():
    for token in ["/channels", "/add", "/run", "/history", "/stop", "/schedule", "/settings",
                  "/prompts", "/prompt", "/files", "/health", "/panic"]:
        assert token in HELP_TEXT, f"{token} missing from HELP_TEXT"


def test_help_documents_the_full_history_window():
    from bot_help import guide_for

    assert "/history" in HELP_TEXT
    assert "all" in guide_for("run")            # the `all` window lives in the guide
    assert "entire history" in guide_for("history")


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


def test_bot_client_sends_plain_text(monkeypatch, tmp_path):
    # Telethon's default parse mode is Markdown, which on some versions pairs the
    # '_' in '/login_phone' and '/set summary_prompt' into italics and mangles the
    # help text. The bot must send plain text.
    import bot
    from types import SimpleNamespace

    monkeypatch.setattr(bot, "BOT_SESSION_BASENAME", str(tmp_path / "bot"))
    client = bot.build_bot_client(SimpleNamespace(api_id=1, api_hash="h"))
    assert client.parse_mode is None
