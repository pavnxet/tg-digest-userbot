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
    token = next(iter(app._channel_panel._tags))
    _run(app.handle_callback(FakeCallbackEvent(bot, f"chlist:{token}:1".encode(), OWNER)))
    assert "page 2/2" in _texts(bot)[-1]


def test_pagination_edits_the_message_and_keeps_its_own_tag(tmp_path):
    # Two lists open at once must not interfere, and paging edits in place.
    text = "\n".join(f"@chan{i} | tags=news" for i in range(21)) + "\n@other | tags=jobs\n"
    app, bot, user, owners, session = build_app(tmp_path, channels_text=text)
    _run(app.handle(FakeEvent(bot, "/channels news", OWNER)))
    news_token = next(iter(app._channel_panel._tags))
    _run(app.handle(FakeEvent(bot, "/channels", OWNER)))          # a second list

    event = FakeCallbackEvent(bot, f"chlist:{news_token}:1".encode(), OWNER)
    _run(app.handle_callback(event))
    assert event.edits, "paging should edit the message, not send a new one"
    assert "tag=news" in event.edits[-1][0]
    assert "page 2/2" in event.edits[-1][0]


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


# --------------------------------------------------------------------------
# Phase 1: stale buttons must never act on the wrong channel
# --------------------------------------------------------------------------
def test_stale_remove_button_does_nothing(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a | News\n@b | News\n")
    _run(app.handle(FakeEvent(bot, "/remove News", OWNER)))      # ambiguous -> buttons
    assert app._channel_panel._picks
    app._channel_panel._picks.clear()                            # simulate expiry
    _run(app.handle_callback(FakeCallbackEvent(bot, b"rm:expired", OWNER)))
    assert "पुराना" in _texts(bot)[-1]
    assert len(app.channels_store.entries()) == 2


def test_stale_channel_list_button_says_so(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n")
    _run(app.handle_callback(FakeCallbackEvent(bot, b"chlist:expired:1", OWNER)))
    assert "पुरानी" in _texts(bot)[-1]


def test_unknown_callback_gets_a_reply(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n")
    _run(app.handle_callback(FakeCallbackEvent(bot, b"totally:unknown", OWNER)))
    assert "पुराना" in _texts(bot)[-1]


def test_remove_by_raw_targets_the_same_channel_after_a_shift(tmp_path):
    # The index moves when a line is deleted; the raw line must not.
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n@b\n@c\n")
    target = app.channels_store.entries()[1]                     # @b
    _run(app._channel_panel._do_remove(FakeEvent(bot, "/x", OWNER), {"raw": target.raw}))
    assert [e.spec.value for e in app.channels_store.entries()] == ["a", "c"]


def test_remove_by_raw_when_the_channel_is_already_gone(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n")
    _run(app._channel_panel._do_remove(FakeEvent(bot, "/x", OWNER), {"raw": "@gone"}))
    assert "नहीं है" in _texts(bot)[-1]
    assert len(app.channels_store.entries()) == 1


def test_unverified_id_confirmation_carries_the_id(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n")
    _run(app.handle(FakeEvent(bot, "/add -1001234567890", OWNER)))
    pending = list(app._pending.values())
    assert pending, "an unverifiable id must ask before adding"
    assert pending[0].payload["resolved_id"] == -1001234567890


def test_add_reports_membership_as_unknown_for_a_public_channel(tmp_path):
    from types import SimpleNamespace

    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n")
    user.entities["news"] = SimpleNamespace(id=1, title="News")
    _run(app.handle(FakeEvent(bot, "/add @news", OWNER)))
    assert "सदस्यता की जाँच नहीं हो सकी" in _texts(bot)[-1]


# --------------------------------------------------------------------------
# Phase 2: say exactly what changed, and suggest the next step
# --------------------------------------------------------------------------
def test_pause_says_which_channel_changed(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a | Alpha\n@b | Beta\n")
    _run(app.handle(FakeEvent(bot, "/pause 2", OWNER)))
    text = _texts(bot)[-1]
    assert "#2" in text and "Beta" in text and "pause" in text


def test_pause_with_a_bad_index_says_so(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n")
    _run(app.handle(FakeEvent(bot, "/pause 9", OWNER)))
    assert "नहीं मिला" in _texts(bot)[-1]


def test_tag_says_what_it_tagged(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a | Alpha\n")
    _run(app.handle(FakeEvent(bot, "/tag 1 news", OWNER)))
    text = _texts(bot)[-1]
    assert "news" in text and "Alpha" in text


def test_channels_empty_suggests_add(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="# only a comment\n")
    _run(app.handle(FakeEvent(bot, "/channels", OWNER)))
    assert "/add" in _texts(bot)[-1]


# --------------------------------------------------------------------------
# Phase 3: filters and resolved titles
# --------------------------------------------------------------------------
def test_channels_paused_filter(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a | A | off\n@b | B\n")
    _run(app.handle(FakeEvent(bot, "/channels paused", OWNER)))
    text = _texts(bot)[-1]
    assert "1." in text and "2." not in text


def test_channels_invalid_filter_excludes_valid_lines(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n!!!\n")
    _run(app.handle(FakeEvent(bot, "/channels invalid", OWNER)))
    assert "@a" not in _texts(bot)[-1]


def test_channels_shows_a_resolved_title_and_caches_it(tmp_path):
    from types import SimpleNamespace

    script = tmp_path / "fake_main.py"
    script.write_text("pass\n", encoding="utf-8")
    app, bot, user, owners, session = build_app(
        tmp_path, channels_text="-1001234567890\n", run_script=script,
    )
    user.entities[-1001234567890] = SimpleNamespace(id=-1001234567890, title="Maths Daily")
    _run(app.handle(FakeEvent(bot, "/channels", OWNER)))
    assert "Maths Daily" in _texts(bot)[-1]
    assert (tmp_path / "cache" / "channel_titles.json").exists()


# --------------------------------------------------------------------------
# Bug hunt: the session must never be touched while a scrape is running
# --------------------------------------------------------------------------
def _fake_running(app):
    from types import SimpleNamespace

    app._run_panel.rm._proc = SimpleNamespace(returncode=None, pid=4242)


def test_channels_does_not_touch_the_session_while_a_run_is_active(tmp_path):
    from types import SimpleNamespace

    script = tmp_path / "fake_main.py"
    script.write_text("pass\n", encoding="utf-8")
    app, bot, user, owners, session = build_app(
        tmp_path, channels_text="-1001234567890\n", run_script=script,
    )
    user.entities[-1001234567890] = SimpleNamespace(id=1, title="X")
    _fake_running(app)
    before = len(user.calls)
    _run(app.handle(FakeEvent(bot, "/channels", OWNER)))
    assert user.calls[before:] == []          # no connect / get_entity / disconnect


def test_add_while_a_run_is_active_asks_instead_of_connecting(tmp_path):
    script = tmp_path / "fake_main.py"
    script.write_text("pass\n", encoding="utf-8")
    app, bot, user, owners, session = build_app(tmp_path, channels_text="@a\n", run_script=script)
    _fake_running(app)
    before = len(user.calls)
    _run(app.handle(FakeEvent(bot, "/add -1001234567890", OWNER)))
    assert user.calls[before:] == []
    assert app._pending                       # it asked "add anyway" instead


def test_ambiguous_remove_tokens_stay_bounded(tmp_path):
    app, bot, user, owners, session = build_app(
        tmp_path, channels_text="\n".join(f"@c{i} | Dup" for i in range(30)) + "\n",
    )
    for _ in range(15):
        _run(app.handle(FakeEvent(bot, "/remove Dup", OWNER)))
    assert len(app._channel_panel._picks) <= 200
