"""Owner lock and pairing."""
import asyncio

from fake_bot_client import FakeEvent, build_app

OWNER = 555
STRANGER = 999


def test_non_owner_gets_no_reply_for_every_command(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, owner_id=OWNER)
    for command in list(app.commands):
        event = FakeEvent(bot, command, STRANGER)
        asyncio.run(app.handle(event))
        assert event.replies == [], f"{command} replied to a non-owner"
    assert bot.sent == []


def test_non_owner_group_message_is_ignored(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, owner_id=OWNER)
    event = FakeEvent(bot, "/status", OWNER, is_private=False)
    asyncio.run(app.handle(event))
    assert event.replies == []
    assert bot.sent == []


def test_owner_can_use_commands(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, owner_id=OWNER)
    event = FakeEvent(bot, "/help", OWNER)
    asyncio.run(app.handle(event))
    assert event.replies


def test_every_command_is_dispatched_only_through_handle():
    # The registry is built once and consumed only inside handle(), so a new
    # handler added to the registry is automatically owner-guarded.
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "bot_handlers.py").read_text(encoding="utf-8")
    assert source.count("self.commands[") == 1


# --------------------------------------------------------------------------
# Pairing
# --------------------------------------------------------------------------
def test_pairing_correct_code_once_then_reused_fails(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, needs_pairing=True)
    code = owners.start_pairing()

    async def scenario():
        good = FakeEvent(bot, f"/claim {code}", 777)
        await app.handle(good)
        assert owners.is_owner(777)
        assert good.replies and good.deleted
        reused = FakeEvent(bot, f"/claim {code}", 888)
        await app.handle(reused)
        assert not owners.is_owner(888)
        assert reused.replies == []

    asyncio.run(scenario())
    from owner import OwnerStore

    assert OwnerStore(tmp_path / "owner.json").owner_id == 777


def test_pairing_wrong_code_fails_silently(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, needs_pairing=True)
    owners.start_pairing()
    event = FakeEvent(bot, "/claim deadbeef", 777)
    asyncio.run(app.handle(event))
    assert not owners.is_owner(777)
    assert event.replies == []


def test_pairing_code_never_appears_in_chat(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, needs_pairing=True)
    code = owners.start_pairing()
    asyncio.run(app.handle(FakeEvent(bot, f"/claim {code}", 777)))
    assert code not in bot.all_text()
    assert code not in str(bot.calls)


def test_before_pairing_everything_is_ignored(tmp_path):
    app, bot, user, owners, session = build_app(tmp_path, needs_pairing=True)
    for command in list(app.commands):
        event = FakeEvent(bot, command, 777)
        asyncio.run(app.handle(event))
        assert event.replies == []
    assert bot.sent == []


def test_owner_file_is_chmod_600(tmp_path):
    import os
    import stat

    app, bot, user, owners, session = build_app(tmp_path, needs_pairing=True)
    owners.claim(777, owners.start_pairing())
    path = tmp_path / "owner.json"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


# --------------------------------------------------------------------------
# Phase 1: the owner file must be written atomically and must fail closed
# --------------------------------------------------------------------------
def test_corrupt_owner_file_does_not_reopen_pairing(tmp_path):
    from owner import OwnerStore

    path = tmp_path / "owner.json"
    path.write_text("{ not json", encoding="utf-8")
    owners = OwnerStore(path)
    assert owners.corrupt is True
    assert owners.needs_pairing() is False      # fail closed
    assert owners.is_owner(OWNER) is False      # nobody is the owner
    assert owners.claim(STRANGER, "anything") is False


def test_missing_owner_file_still_allows_pairing(tmp_path):
    from owner import OwnerStore

    owners = OwnerStore(tmp_path / "owner.json")
    assert owners.corrupt is False
    assert owners.needs_pairing() is True


def test_owner_save_is_atomic_and_leaves_no_tmp_file(tmp_path):
    import json

    from owner import OwnerStore

    path = tmp_path / "owner.json"
    owners = OwnerStore(path)
    code = owners.start_pairing()
    assert owners.claim(OWNER, code) is True
    assert json.loads(path.read_text(encoding="utf-8"))["owner_id"] == OWNER
    assert not list(tmp_path.glob("*.tmp"))


def test_corrupt_owner_file_ignores_every_command(tmp_path):
    # A corrupt file must not let a stranger take over via /claim.
    from owner import OwnerStore

    path = tmp_path / "owner.json"
    path.write_text("garbage", encoding="utf-8")
    owners = OwnerStore(path)
    app, bot, user, owner_store, session = build_app(tmp_path, owner_id=None, needs_pairing=False)
    app.owners = owners
    asyncio.run(app.handle(FakeEvent(bot, "/claim abc", STRANGER)))
    assert not bot.sent
