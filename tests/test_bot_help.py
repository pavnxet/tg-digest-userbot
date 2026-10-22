"""The help system: HTML formatting, per-command guides, and their coverage."""
import asyncio
import re

import pytest

from bot_handlers import COMMAND_MENU
from bot_help import COMMAND_GUIDES, GUIDE_COMMANDS, HELP_TEXT, guide_for
from fake_bot_client import FakeEvent, build_app

OWNER = 555

# Telegram accepts only this small subset in HTML mode.
_ALLOWED_TAG = re.compile(r"</?(?:b|i|u|s|code|pre|a)(?:\s[^>]*)?>")
_ENTITY = re.compile(r"&(?:amp|lt|gt|quot);")


def _unescaped_markup(text: str) -> str:
    """Whatever is left after removing valid tags and entities.

    Any remaining '<' or '&' would make Telegram reject the message.
    """
    rest = _ENTITY.sub("", _ALLOWED_TAG.sub("", text))
    return "".join(ch for ch in rest if ch in "<&")


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------
def test_help_text_is_valid_telegram_html():
    assert _unescaped_markup(HELP_TEXT) == ""


def test_every_guide_is_valid_telegram_html():
    for name, guide in COMMAND_GUIDES.items():
        assert _unescaped_markup(guide) == "", f"{name} has unescaped markup"


def test_help_text_has_visible_structure():
    # Bold section headers and italic hints -- not one flat wall of text.
    assert "<b>" in HELP_TEXT
    assert "<i>" in HELP_TEXT
    assert HELP_TEXT.count("<b>") >= 6


def test_help_text_fits_one_telegram_message():
    assert len(HELP_TEXT) < 4096
    for guide in COMMAND_GUIDES.values():
        assert len(guide) < 4096


def test_help_escapes_the_angle_bracket_placeholders():
    # '<#|@name|link>' must be escaped or Telegram drops the whole message.
    assert "&lt;" in COMMAND_GUIDES["history"]
    assert "<#" not in COMMAND_GUIDES["history"]


# --------------------------------------------------------------------------
# Coverage: every command the menu offers has a guide
# --------------------------------------------------------------------------
def test_every_menu_command_has_a_guide():
    missing = {name for name, _ in COMMAND_MENU} - set(COMMAND_GUIDES)
    assert not missing, f"commands with no guide: {sorted(missing)}"


def test_every_guide_command_has_a_guide():
    assert set(COMMAND_GUIDES) >= GUIDE_COMMANDS


def test_guide_for_accepts_a_leading_slash_and_case():
    assert guide_for("/run") == COMMAND_GUIDES["run"]
    assert guide_for("RUN") == COMMAND_GUIDES["run"]
    assert guide_for("nope") is None
    assert guide_for("") is None


def test_help_text_lists_every_command():
    for name, _description in COMMAND_MENU:
        assert f"/{name}" in HELP_TEXT or name in ("start", "help"), f"{name} missing"


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------
def _app(tmp_path):
    """A fully-wired app, so every panel's commands are registered."""
    script = tmp_path / "fake_main.py"
    script.write_text("pass\n", encoding="utf-8")
    app, bot, _user, _owners, _session = build_app(
        tmp_path, channels_text="@news | News\n", run_script=script,
    )
    return app, bot


def test_start_sends_the_help_text_as_html(tmp_path):
    app, bot = _app(tmp_path)
    event = FakeEvent(bot, "/start", OWNER)
    asyncio.run(app.handle(event))
    assert bot.sent[-1][1] == HELP_TEXT
    assert event.parse_modes[-1] == "html"


def test_help_without_argument_sends_the_command_list(tmp_path):
    app, bot = _app(tmp_path)
    event = FakeEvent(bot, "/help", OWNER)
    asyncio.run(app.handle(event))
    assert bot.sent[-1][1] == HELP_TEXT


@pytest.mark.parametrize("command", sorted(GUIDE_COMMANDS))
def test_bare_command_shows_its_guide(tmp_path, command):
    # Tapping a command in the Telegram menu sends it with no arguments.
    app, bot = _app(tmp_path)
    event = FakeEvent(bot, f"/{command}", OWNER)
    asyncio.run(app.handle(event))
    assert bot.sent[-1][1] == COMMAND_GUIDES[command]
    assert event.parse_modes[-1] == "html"


def test_help_with_a_command_sends_that_guide(tmp_path):
    app, bot = _app(tmp_path)
    event = FakeEvent(bot, "/help run", OWNER)
    asyncio.run(app.handle(event))
    assert bot.sent[-1][1] == COMMAND_GUIDES["run"]
    assert event.parse_modes[-1] == "html"


def test_help_with_an_unknown_command_says_so(tmp_path):
    app, bot = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/help nope", OWNER)))
    assert "nope" in bot.sent[-1][1]
    assert "/help" in bot.sent[-1][1]


def test_a_command_with_arguments_still_runs(tmp_path):
    # /channels takes no argument requirement, so it must run, not show a guide.
    app, bot = _app(tmp_path)
    event = FakeEvent(bot, "/channels", OWNER)
    asyncio.run(app.handle(event))
    assert bot.sent[-1][1] != COMMAND_GUIDES["channels"]


def test_guides_are_owner_only(tmp_path):
    app, bot = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/help run", OWNER + 1)))
    assert not bot.sent
