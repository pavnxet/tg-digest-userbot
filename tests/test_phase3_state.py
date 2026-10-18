"""Phase 3: state.json and run-id handling."""
from datetime import datetime, timezone

from state import (
    get_last_run,
    get_run_id,
    load_state,
    save_state,
    set_last_run,
    set_run_id,
)

UTC = timezone.utc


def test_set_and_get_last_run(tmp_path):
    path = tmp_path / "state.json"
    moment = datetime(2026, 10, 18, 6, 0, tzinfo=UTC)
    set_last_run(path, moment)
    assert get_last_run(path) == moment


def test_get_last_run_missing_file(tmp_path):
    assert get_last_run(tmp_path / "nope.json") is None


def test_get_last_run_corrupt_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert get_last_run(path) is None
    assert load_state(path) == {}


def test_get_last_run_empty_file(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("", encoding="utf-8")
    assert get_last_run(path) is None


def test_get_last_run_hand_edited_missing_key(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, {"something": "else"})
    assert get_last_run(path) is None


def test_get_last_run_naive_timestamp_is_utc(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, {"last_run_utc": "2026-10-18T06:00:00"})  # no offset
    assert get_last_run(path) == datetime(2026, 10, 18, 6, 0, tzinfo=UTC)


def test_last_run_preserved_across_updates(tmp_path):
    path = tmp_path / "state.json"
    set_last_run(path, datetime(2026, 10, 17, tzinfo=UTC))
    save_state(path, {**load_state(path), "extra": 1})
    set_last_run(path, datetime(2026, 10, 18, tzinfo=UTC))
    data = load_state(path)
    assert data["extra"] == 1
    assert data["last_run_utc"].startswith("2026-10-18")


def test_run_id_round_trip(tmp_path):
    assert get_run_id(tmp_path) is None
    set_run_id(tmp_path, "20261018_1200")
    assert get_run_id(tmp_path) == "20261018_1200"


def test_state_file_is_plain_utf8_json(tmp_path):
    path = tmp_path / "state.json"
    set_last_run(path, datetime(2026, 10, 18, 6, 0, tzinfo=UTC))
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")  # no BOM
    assert b"last_run_utc" in raw
