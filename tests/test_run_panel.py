"""Section C: /run /stop /preview /last via a fake main.py subprocess."""
import asyncio

from fake_bot_client import FakeEvent, build_app

OWNER = 555


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def _make_script(tmp_path, body: str):
    script = tmp_path / "fake_main.py"
    script.write_text(body, encoding="utf-8")
    return script


def _success_script(tmp_path, outdir):
    return _make_script(tmp_path, (
        "import pathlib, json\n"
        f"p = pathlib.Path(r'{outdir}')\n"
        "p.mkdir(exist_ok=True)\n"
        "(p / 'digest_2026-10-18_1200.json').write_text(json.dumps("
        "{'meta': {'channels_ok': 2, 'messages_total': 5, 'skipped': []}}))\n"
    ))


def _sleep_script(tmp_path):
    return _make_script(tmp_path, "import time\ntime.sleep(30)\n")


def _preview_script(tmp_path):
    return _make_script(tmp_path, "print('  OK    News  (~12 message(s) in window)')\nprint('  OK    Jobs  (~3 message(s) in window)')\n")


def _app(tmp_path, script, channels="@a\n@b\n"):
    outdir = tmp_path / "output"
    cachedir = tmp_path / "cache"
    return build_app(tmp_path, channels_text=channels, run_script=script,
                     output_dir=outdir, cache_dir=cachedir)


def test_run_success_sends_digest(tmp_path):
    outdir = tmp_path / "output"
    script = _success_script(tmp_path, outdir)
    app, bot, user, owners, session = _app(tmp_path, script)

    async def scenario():
        await app.handle(FakeEvent(bot, "/run today", OWNER))
        await app._run_panel._task

    asyncio.run(scenario())
    assert any("Run ok" in t for t in _texts(bot))
    assert bot.files                                  # digest sent as a document
    assert user.connected is False                    # user client disconnected before spawn


def test_run_stop_kills(tmp_path):
    script = _sleep_script(tmp_path)
    app, bot, user, owners, session = _app(tmp_path, script)

    async def scenario():
        await app.handle(FakeEvent(bot, "/run today", OWNER))
        await asyncio.sleep(0.3)
        await app.handle(FakeEvent(bot, "/stop", OWNER))
        await app._run_panel._task

    asyncio.run(scenario())
    assert any("रोक दिया" in t for t in _texts(bot))


def test_run_refused_while_running(tmp_path):
    script = _sleep_script(tmp_path)
    app, bot, user, owners, session = _app(tmp_path, script)

    async def scenario():
        await app.handle(FakeEvent(bot, "/run today", OWNER))
        await asyncio.sleep(0.2)
        await app.handle(FakeEvent(bot, "/run today", OWNER))
        await app.handle(FakeEvent(bot, "/stop", OWNER))
        await app._run_panel._task

    asyncio.run(scenario())
    assert any("पहले से चल रहा" in t for t in _texts(bot))


def test_run_rejects_bad_arguments(tmp_path):
    script = _success_script(tmp_path, tmp_path / "output")
    app, bot, user, owners, session = _app(tmp_path, script)
    asyncio.run(app.handle(FakeEvent(bot, "/run nonsense", OWNER)))
    assert any("❌" in t for t in _texts(bot))
    asyncio.run(app.handle(FakeEvent(bot, "/run today --auto-join", OWNER)))
    assert any("अज्ञात option" in t for t in _texts(bot))


def test_preview_shows_counts(tmp_path):
    script = _preview_script(tmp_path)
    app, bot, user, owners, session = _app(tmp_path, script)
    asyncio.run(app.handle(FakeEvent(bot, "/preview today", OWNER)))
    text = _texts(bot)[-1]
    assert "News: ~12" in text and "Jobs: ~3" in text


def test_last_resends(tmp_path):
    outdir = tmp_path / "output"
    script = _success_script(tmp_path, outdir)
    app, bot, user, owners, session = _app(tmp_path, script)

    async def scenario():
        await app.handle(FakeEvent(bot, "/run today", OWNER))
        await app._run_panel._task

    asyncio.run(scenario())
    before = len(bot.files)
    asyncio.run(app.handle(FakeEvent(bot, "/last", OWNER)))
    assert len(bot.files) > before


def test_run_commands_are_owner_guarded(tmp_path):
    script = _success_script(tmp_path, tmp_path / "output")
    app, bot, user, owners, session = _app(tmp_path, script)
    for command in ["/run today", "/stop", "/preview today", "/last"]:
        event = FakeEvent(bot, command, 999)
        asyncio.run(app.handle(event))
        assert event.replies == []
    assert bot.sent == []


# --------------------------------------------------------------------------
# Phase 2: the summary must say where the files are and how to get them
# --------------------------------------------------------------------------
def test_summary_says_where_the_files_are(tmp_path):
    from types import SimpleNamespace

    app, bot, user, owners, session = build_app(tmp_path, run_script=_sleep_script(tmp_path))
    panel = app._run_panel
    out = tmp_path / "output" / "digest_x.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{}", encoding="utf-8")
    panel._last_outputs = [out]

    text = panel._summary(SimpleNamespace(result="ok", duration=12.0), send_files=False)
    assert "output/" in text          # the folder
    assert "/last" in text            # how to get the file when it was not sent

    sent = panel._summary(SimpleNamespace(result="ok", duration=12.0), send_files=True)
    assert "/last" not in sent        # no hint when the file was already sent


# --------------------------------------------------------------------------
# Phase 3: the Resume button
# --------------------------------------------------------------------------
def _panel(tmp_path):
    from types import SimpleNamespace

    app, bot, user, owners, session = build_app(tmp_path, run_script=_sleep_script(tmp_path))
    return app, bot, app._run_panel, SimpleNamespace


def test_resume_button_only_when_a_run_did_not_finish(tmp_path):
    app, bot, panel, ns = _panel(tmp_path)
    panel._last_argv = ["--today"]
    assert panel._resume_buttons(ns(result="ok")) is None
    buttons = panel._resume_buttons(ns(result="timeout"))
    assert buttons and "Resume" in buttons[0][0].text


def test_resume_button_continues_the_same_run(tmp_path):
    from fake_bot_client import FakeCallbackEvent

    app, bot, panel, ns = _panel(tmp_path)
    panel._last_argv = ["--last", "3d"]
    panel._last_send_files = False
    panel._resume_buttons(ns(result="timeout"))
    token = next(iter(panel._resume))

    calls = []

    async def fake_start(argv, capture=False, timeout=None):
        calls.append(list(argv))
        return None

    async def fake_supervise(chat_id, send_files):
        return None

    panel.rm.start = fake_start
    panel._supervise = fake_supervise
    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, f"resume:{token}".encode(), OWNER)))

    assert calls, "resume should start a run"
    assert "--resume" in calls[0]
    assert "--last" in calls[0] and "3d" in calls[0]      # same window as before


def test_stale_resume_button_says_so(tmp_path):
    from fake_bot_client import FakeCallbackEvent

    app, bot, panel, ns = _panel(tmp_path)
    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, b"resume:expired", OWNER)))
    assert "पुराना" in _texts(bot)[-1]
