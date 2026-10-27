"""Section D: schedule store, /schedule commands and the scheduler."""
import asyncio
from datetime import datetime

from fake_bot_client import FakeEvent, build_app
from schedule_store import ScheduleStore
from timeutil import get_tz

OWNER = 555
TZ = get_tz()
NOON = datetime(2026, 10, 18, 12, 0, tzinfo=TZ)


def _texts(bot):
    return [text for _chat, text, _buttons, _message in bot.sent]


def _script(tmp_path, body="pass\n"):
    path = tmp_path / "fake_main.py"
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------
def test_add_validates_time_and_window(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.json")
    assert store.add("09:00", "today")[0] is True
    assert store.add("25:00", "today")[0] is False
    assert store.add("9:0", "today")[0] is False
    assert store.add("10:00", "")[0] is False


def test_cap_and_remove_and_pause(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.json")
    for i in range(5):
        assert store.add(f"0{i}:00", "today")[0] is True
    assert store.add("09:00", "today")[0] is False       # cap 5
    assert store.set_paused(1, True)[0] is True
    assert store.all()[0].paused is True
    assert store.remove(1)[0] is True
    assert len(store.all()) == 4
    assert store.remove(99)[0] is False


def test_corrupt_file_is_ignored(tmp_path):
    path = tmp_path / "schedule.json"
    path.write_text("{ not json", encoding="utf-8")
    assert ScheduleStore(path).all() == []


def test_due_and_missed_and_next(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.json")
    store.add("12:00", "today")
    store.add("11:00", "24h")
    store.add("23:00", "3d")
    assert [s.time for _, s in store.due(NOON)] == ["12:00"]
    assert [s.time for _, s in store.missed(NOON)] == ["11:00"]
    # at 10:00 the next fire is 11:00; at noon 12:00 is 'now', so next is 23:00
    ten = NOON.replace(hour=10)
    assert store.next_run(ten)[0].time == "11:00"
    assert store.next_run(NOON)[0].time == "23:00"
    # fired today -> no longer due or missed
    store.mark_fired(1, NOON)
    assert store.due(NOON) == []


def test_paused_is_skipped(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.json")
    store.add("12:00", "today")
    store.set_paused(1, True)
    assert store.due(NOON) == [] and store.missed(NOON) == []
    assert store.next_run(NOON) is None


# --------------------------------------------------------------------------
# /schedule commands
# --------------------------------------------------------------------------
def _app(tmp_path, body="pass\n"):
    # The channel carries the `news` tag so /schedule add group:news is valid.
    return build_app(tmp_path, channels_text="@a | A | tags=news\n", run_script=_script(tmp_path, body))


def test_schedule_commands(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/schedule add 09:00 today group:news", OWNER)))
    assert "✅" in _texts(bot)[-1]
    asyncio.run(app.handle(FakeEvent(bot, "/schedule list", OWNER)))
    assert "09:00" in _texts(bot)[-1] and "group:news" in _texts(bot)[-1]
    asyncio.run(app.handle(FakeEvent(bot, "/schedule pause 1", OWNER)))
    assert app._schedule_panel.store.all()[0].paused is True
    asyncio.run(app.handle(FakeEvent(bot, "/schedule resume 1", OWNER)))
    assert app._schedule_panel.store.all()[0].paused is False


def test_schedule_remove_needs_confirmation(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/schedule add 09:00 today", OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, "/schedule remove 1", OWNER)))
    assert "हटाएँ" in _texts(bot)[-1]
    token = list(app._pending)[0]
    from fake_bot_client import FakeCallbackEvent

    asyncio.run(app.handle_callback(FakeCallbackEvent(bot, f"confirm:{token}".encode(), OWNER)))
    assert app._schedule_panel.store.all() == []


def test_schedule_bad_time_rejected(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/schedule add 99:99 today", OWNER)))
    assert "❌" in _texts(bot)[-1]


def test_schedule_commands_owner_guarded(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    event = FakeEvent(bot, "/schedule list", 999)
    asyncio.run(app.handle(event))
    assert event.replies == [] and bot.sent == []


# --------------------------------------------------------------------------
# Scheduler
# --------------------------------------------------------------------------
def test_scheduler_fires_due_run(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    scheduler = app._scheduler
    now = datetime.now(TZ)
    app._schedule_panel.store.add(f"{now.hour:02d}:{now.minute:02d}", "today")

    async def scenario():
        for index, sched in scheduler.store.due(now):
            await scheduler._fire(index, sched)
        await app._run_panel._task

    asyncio.run(scenario())
    assert any("Run ok" in t for t in _texts(bot))
    assert app._schedule_panel.store.all()[0].last_fired is not None


def test_scheduler_skips_when_busy(tmp_path):
    app, bot, user, owners, session = _app(tmp_path, body="import time\ntime.sleep(30)\n")
    scheduler = app._scheduler
    now = datetime.now(TZ)
    app._schedule_panel.store.add(f"{now.hour:02d}:{now.minute:02d}", "today")

    async def scenario():
        await app._run_panel.start_run("today", OWNER)     # occupy the runner
        await asyncio.sleep(0.2)
        for index, sched in scheduler.store.due(now):
            await scheduler._fire(index, sched)
        await app.handle(FakeEvent(bot, "/stop", OWNER))
        await app._run_panel._task

    asyncio.run(scenario())
    assert any("skip" in t for t in _texts(bot))


def test_missed_run_offers_run_now(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    scheduler = app._scheduler
    app._schedule_panel.store.add("11:00", "today")
    asyncio.run(scheduler.startup_check(NOON))
    assert any("छूट गया" in t for t in _texts(bot))
    # the "Run now" button triggers a run
    from fake_bot_client import FakeCallbackEvent

    async def scenario():
        await app.handle_callback(FakeCallbackEvent(bot, b"sched:run:1", OWNER))
        await app._run_panel._task

    asyncio.run(scenario())
    assert any("Run ok" in t for t in _texts(bot))


# --------------------------------------------------------------------------
# Richer /schedule list, and stricter /schedule add
# --------------------------------------------------------------------------
def test_schedule_list_shows_next_run_and_scope(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/schedule add 09:00 24h group:news", OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, "/schedule list", OWNER)))
    text = _texts(bot)[-1]
    assert "1/5" in text                    # how many of the maximum
    assert "group:news" in text             # the scope
    assert "अगली बार" in text                # next run
    assert "बाद" in text                     # with a countdown


def test_schedule_list_marks_paused_entries(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/schedule add 09:00 today", OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, "/schedule pause 1", OWNER)))
    asyncio.run(app.handle(FakeEvent(bot, "/schedule list", OWNER)))
    text = _texts(bot)[-1]
    assert "⏸" in text
    assert "resume 1" in text               # tells you how to switch it back on


def test_schedule_list_shows_the_last_fire_date(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.json")
    store.add("09:00", "today")
    store.mark_fired(1, NOON)
    app, bot, user, owners, session = _app(tmp_path)
    app._schedule_panel.store._items = store.all()      # same objects
    asyncio.run(app.handle(FakeEvent(bot, "/schedule list", OWNER)))
    assert "पिछली बार" in _texts(bot)[-1]


def test_schedule_add_rejects_an_unknown_window(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/schedule add 09:00 banana", OWNER)))
    assert "window गलत है" in _texts(bot)[-1]
    assert app._schedule_panel.store.all() == []


def test_schedule_add_rejects_an_unknown_tag(tmp_path):
    app, bot, user, owners, session = _app(tmp_path)
    asyncio.run(app.handle(FakeEvent(bot, "/schedule add 09:00 today group:nope", OWNER)))
    text = _texts(bot)[-1]
    assert "nope" in text and "news" in text      # names the tags that do exist
    assert app._schedule_panel.store.all() == []


def test_store_rejects_an_unknown_window(tmp_path):
    store = ScheduleStore(tmp_path / "schedule.json")
    assert store.add("09:00", "banana")[0] is False
    assert store.add("09:00", "all")[0] is True
