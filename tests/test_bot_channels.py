"""Section B: /channels /add /remove /pause /resume /tag /untag /groups."""
import asyncio
from types import SimpleNamespace

from fake_bot_client import FakeCallbackEvent, FakeEvent, build_app

OWNER = 555


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def _run(coro):
    return asyncio.run(coro)


def _press_confirm(app, bot):
    """Press the confirm button of the single pending confirmation."""
    token = list(app._pending)[0]
    _run(app.handle_callback(FakeCallbackEvent(bot, f"confirm:{token}".encode(), OWNER)))


def test_channels_lists_and_paginates(tmp_path):
    text = "\n".join(f"@chan{i}" for i in range(21)) + "\n"
    app, bot, user, owners, session = build_app(tmp_path, channels_text=text)
    _run(app.handle(FakeEvent(bot, "/channels", OWNER)))
    assert "page 1/2" in _texts(bot)[-1]
    _run(app.handle_callback(FakeCallbackEvent(bot, b"chlist:next", OWNER)))
    assert "page 2/2" in _texts(bot)[-1]


def test_channels_filters_by_tag(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a | tags=news\n@b | tags=jobs\n")
    _run(app.handle(FakeEvent(bot, "/channels news", OWNER)))
    text = _texts(bot)[-1]
    assert "1." in text and "2." not in text


def test_add_valid_resolves_and_appends(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n")
    user.entities["news"] = SimpleNamespace(id=123, title="News Channel")
    _run(app.handle(FakeEvent(bot, "/add @news | My News", OWNER)))
    assert "जोड़ा" in _texts(bot)[-1]
    assert "news" in (tmp_path / "channels.txt").read_text(encoding="utf-8")


def test_add_rejects_injection(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n")
    for bad in ["/add ", "/add @x\n@y", "/add @z\x00"]:
        _run(app.handle(FakeEvent(bot, bad, OWNER)))
    assert not any("जोड़ा" in t for t in _texts(bot))


def test_add_duplicate_is_reported(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@news\n")
    user.entities["news"] = SimpleNamespace(id=1, title="News")
    _run(app.handle(FakeEvent(bot, "/add https://t.me/news", OWNER)))
    assert "पहले से" in _texts(bot)[-1]


def test_add_unverified_id_offers_add_anyway(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n")
    # user.get_entity will raise -> unverified
    _run(app.handle(FakeEvent(bot, "/add -1001234567890", OWNER)))
    assert bot.sent[-1][2] is not None          # buttons offered
    _press_confirm(app, bot)
    assert "unverified" in _texts(bot)[-1].lower() or "जोड़" in _texts(bot)[-1]


def test_remove_by_number_requires_confirmation(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n@b\n")
    _run(app.handle(FakeEvent(bot, "/remove 1", OWNER)))
    assert "हटाएँ" in _texts(bot)[-1]
    _press_confirm(app, bot)
    assert "@a" not in (tmp_path / "channels.txt").read_text(encoding="utf-8")


def test_remove_ambiguous_asks_which(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a | Dup\n@b | Dup\n")
    _run(app.handle(FakeEvent(bot, "/remove Dup", OWNER)))
    assert "कौन सा" in _texts(bot)[-1]
    assert len(bot.sent[-1][2]) == 2            # one button per match


def test_pause_resume_tag_untag(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n")
    _run(app.handle(FakeEvent(bot, "/pause 1", OWNER)))
    _run(app.handle(FakeEvent(bot, "/tag 1 news", OWNER)))
    body = (tmp_path / "channels.txt").read_text(encoding="utf-8")
    assert "off" in body and "tags=news" in body
    _run(app.handle(FakeEvent(bot, "/resume 1", OWNER)))
    _run(app.handle(FakeEvent(bot, "/untag 1 news", OWNER)))
    body = (tmp_path / "channels.txt").read_text(encoding="utf-8")
    assert "off" not in body and "tags=" not in body


def test_groups_lists_tags(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a | tags=news\n@b | tags=news,jobs\n")
    _run(app.handle(FakeEvent(bot, "/groups", OWNER)))
    text = _texts(bot)[-1]
    assert "#news — 2" in text and "#jobs — 1" in text


def test_channel_commands_are_owner_guarded(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n")
    channel_cmds = ["/channels", "/add @x", "/remove 1", "/pause 1", "/resume 1", "/tag 1 x", "/untag 1 x", "/groups"]
    for command in channel_cmds:
        event = FakeEvent(bot, command, 999)     # not the owner
        _run(app.handle(event))
        assert event.replies == [], f"{command} replied to a non-owner"
    assert bot.sent == []
