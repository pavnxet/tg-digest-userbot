"""Bot: /prompts, /prompt, /set summary_prompt, and the prompt name reaching argv."""
import asyncio
import json
from datetime import datetime

from fake_bot_client import FakeEvent, build_app
from prompts import available_names, load_prompt
from timeutil import get_tz

OWNER = 555
TZ = get_tz()


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def _argv_script(tmp_path, cache_dir):
    script = tmp_path / "fake_main.py"
    script.write_text(
        "import sys, json, pathlib\n"
        f"p = pathlib.Path(r'{cache_dir}')\n"
        "p.mkdir(exist_ok=True)\n"
        "(p / 'argv.json').write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    return script


def _app(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(exist_ok=True)
    script = _argv_script(tmp_path, cache_dir)
    app, bot, user, owners, session = build_app(
        tmp_path, channels_text="@a\n@b\n", run_script=script,
        output_dir=tmp_path / "output", cache_dir=cache_dir,
    )
    return app, bot, cache_dir


# --------------------------------------------------------------------------
# /prompts
# --------------------------------------------------------------------------
def test_prompts_lists_names_and_selection(tmp_path):
    app, bot, cache_dir = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/prompts", OWNER)))
    text = _texts(bot)[-1]
    for name in available_names():
        assert name in text
    assert "selected: default" in text


def test_prompts_reflects_the_selected_prompt(tmp_path):
    app, bot, cache_dir = _app(tmp_path)
    app._settings_panel.settings.set("summary_prompt", "exam_alerts")
    asyncio.run(app.handle(FakeEvent(bot, "/prompts", OWNER)))
    assert "selected: exam_alerts" in _texts(bot)[-1]


# --------------------------------------------------------------------------
# /prompt
# --------------------------------------------------------------------------
def test_prompt_sends_short_prompt_as_a_message(tmp_path):
    app, bot, cache_dir = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/prompt default", OWNER)))
    assert "This file is JSON" in _texts(bot)[-1]
    assert not bot.files


def test_prompt_sends_long_prompt_as_a_document(tmp_path, monkeypatch):
    import bot_prompts

    monkeypatch.setattr(bot_prompts, "MAX_MESSAGE_CHARS", 100)
    app, bot, cache_dir = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/prompt detailed", OWNER)))
    assert bot.files and not any("This file is JSON" in t for t in _texts(bot))


def test_prompt_unknown_name_is_rejected(tmp_path):
    app, bot, cache_dir = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/prompt ../secret", OWNER)))
    assert "❌" in _texts(bot)[-1] and "Available" in _texts(bot)[-1]


# --------------------------------------------------------------------------
# /set summary_prompt
# --------------------------------------------------------------------------
def test_set_summary_prompt_valid_and_invalid(tmp_path):
    app, bot, cache_dir = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/set summary_prompt exam_alerts", OWNER)))
    assert "✅" in _texts(bot)[-1]
    assert app._settings_panel.settings.get("summary_prompt") == "exam_alerts"
    asyncio.run(app.handle(FakeEvent(bot, "/set summary_prompt ../../etc", OWNER)))
    assert "❌" in _texts(bot)[-1]
    assert app._settings_panel.settings.get("summary_prompt") == "exam_alerts"


# --------------------------------------------------------------------------
# owner-only
# --------------------------------------------------------------------------
def test_prompt_commands_are_owner_guarded(tmp_path):
    app, bot, cache_dir = _app(tmp_path)
    for command in ["/prompts", "/prompt default", "/set summary_prompt exam_alerts"]:
        event = FakeEvent(bot, command, 999)
        asyncio.run(app.handle(event))
        assert event.replies == []
    assert bot.sent == []


# --------------------------------------------------------------------------
# /run and the scheduler pass the selected name
# --------------------------------------------------------------------------
def test_run_passes_the_selected_prompt(tmp_path):
    app, bot, cache_dir = _app(tmp_path)
    app._settings_panel.settings.set("summary_prompt", "exam_alerts")

    async def scenario():
        await app.handle(FakeEvent(bot, "/run today", OWNER))
        await app._run_panel._task

    asyncio.run(scenario())
    argv = json.loads((cache_dir / "argv.json").read_text(encoding="utf-8"))
    assert "--summary-prompt" in argv
    assert argv[argv.index("--summary-prompt") + 1] == "exam_alerts"


def test_scheduler_passes_the_selected_prompt(tmp_path):
    app, bot, cache_dir = _app(tmp_path)
    app._settings_panel.settings.set("summary_prompt", "detailed")
    now = datetime.now(TZ)
    app._schedule_panel.store.add(f"{now.hour:02d}:{now.minute:02d}", "today")

    async def scenario():
        for index, sched in app._scheduler.store.due(now):
            await app._scheduler._fire(index, sched)
        await app._run_panel._task

    asyncio.run(scenario())
    argv = json.loads((cache_dir / "argv.json").read_text(encoding="utf-8"))
    assert argv[argv.index("--summary-prompt") + 1] == "detailed"


# --------------------------------------------------------------------------
# injection
# --------------------------------------------------------------------------
def test_chat_text_cannot_inject_a_prompt_or_autojoin(tmp_path):
    app, bot, cache_dir = _app(tmp_path)
    for command in ["/run today --summary-prompt ../../etc",
                    "/run today prompt:../../etc",
                    "/run today --auto-join"]:
        asyncio.run(app.handle(FakeEvent(bot, command, OWNER)))
        assert "अज्ञात option" in _texts(bot)[-1], command
    assert not (cache_dir / "argv.json").exists()


def test_the_embedded_prompt_is_the_real_file(tmp_path):
    app, bot, cache_dir = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/prompt default", OWNER)))
    assert load_prompt("default") in _texts(bot)[-1]
