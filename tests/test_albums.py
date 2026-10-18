"""Album (grouped_id) merging and media labelling."""
from scraper import merge_albums


def _msg(mid, text=None, gid=None, media=None):
    item = {"id": mid, "t": f"2026-10-18 09:{mid:02d}"}
    if text:
        item["text"] = text
    if media:
        item["media"] = media
    if gid:
        item["_gid"] = gid
    return item


def test_album_is_merged_into_one_message():
    messages = [
        _msg(1, text="first caption", gid=99, media="photo"),
        _msg(2, text="second caption", gid=99, media="photo"),
        _msg(3, text="third caption", gid=99, media="photo"),
        _msg(4, text="standalone"),
    ]
    merged = merge_albums(messages)
    assert len(merged) == 2

    album = merged[0]
    assert album["id"] == 1
    assert album["media"] == "photo x3"
    assert "first caption" in album["text"]
    assert "second caption" in album["text"]
    assert "third caption" in album["text"]
    assert "_gid" not in album

    assert merged[1]["id"] == 4
    assert "_gid" not in merged[1]


def test_non_album_messages_are_untouched():
    messages = [_msg(1, text="a"), _msg(2, text="b")]
    merged = merge_albums(messages)
    assert [m["id"] for m in merged] == [1, 2]


def test_mixed_media_album_reports_both_types():
    messages = [
        _msg(1, text="x", gid=7, media="photo"),
        _msg(2, text="y", gid=7, media="video"),
    ]
    merged = merge_albums(messages)
    assert merged[0]["media"] == "photo+video x2"


def test_album_without_captions_has_no_text_key():
    messages = [_msg(1, gid=5, media="photo"), _msg(2, gid=5, media="photo")]
    merged = merge_albums(messages)
    assert "text" not in merged[0]
    assert merged[0]["media"] == "photo x2"
