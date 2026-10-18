"""Phase 3: album merging, dedupe and --min-chars."""
from scraper import dedupe_messages, filter_min_chars, merge_albums


def _m(mid, text=None, gid=None, media=None):
    item = {"id": mid, "t": f"2026-10-18 09:{mid:02d}"}
    if text:
        item["text"] = text
    if media:
        item["media"] = media
    if gid:
        item["_gid"] = gid
    return item


# --------------------------------------------------------------------------
# Albums
# --------------------------------------------------------------------------
def test_album_merges_into_one_message():
    merged = merge_albums([_m(1, "a", 9, "photo"), _m(2, "b", 9, "photo"), _m(3, "c", 9, "photo")])
    assert len(merged) == 1
    assert merged[0]["media"] == "photo x3"
    assert all(x in merged[0]["text"] for x in "abc")
    assert "_gid" not in merged[0]


def test_caption_only_on_first_item():
    merged = merge_albums([_m(1, "caption", 9, "photo"), _m(2, None, 9, "photo")])
    assert merged[0]["text"] == "caption"


def test_caption_only_on_later_item():
    merged = merge_albums([_m(1, None, 9, "photo"), _m(2, "late caption", 9, "photo")])
    assert merged[0]["text"] == "late caption"


def test_album_with_no_caption_has_no_text():
    merged = merge_albums([_m(1, None, 9, "photo"), _m(2, None, 9, "photo")])
    assert "text" not in merged[0]


def test_two_adjacent_albums_stay_separate():
    merged = merge_albums(
        [_m(1, "a", 9, "photo"), _m(2, "b", 9, "photo"), _m(3, "c", 8, "photo"), _m(4, "d", 8, "photo")]
    )
    assert len(merged) == 2
    assert merged[0]["media"] == "photo x2" and merged[1]["media"] == "photo x2"


def test_album_split_across_interval_keeps_present_items():
    # Only two of a four-item album fall inside the window.
    merged = merge_albums([_m(1, "a", 9, "photo"), _m(2, "b", 9, "photo")])
    assert merged[0]["media"] == "photo x2"


def test_mixed_media_album():
    merged = merge_albums([_m(1, "x", 7, "photo"), _m(2, "y", 7, "video")])
    assert merged[0]["media"] == "photo+video x2"


def test_non_album_messages_untouched():
    merged = merge_albums([_m(1, "a"), _m(2, "b")])
    assert [m["id"] for m in merged] == [1, 2]


def test_album_links_merged_and_deduped():
    a = _m(1, "x", 9, "photo"); a["links"] = ["https://a", "https://b"]
    b = _m(2, "y", 9, "photo"); b["links"] = ["https://b", "https://c"]
    merged = merge_albums([a, b])
    assert merged[0]["links"] == ["https://a", "https://b", "https://c"]


# --------------------------------------------------------------------------
# Dedupe
# --------------------------------------------------------------------------
def test_exact_duplicates_removed_first_kept():
    msgs = [{"id": 1, "text": "same"}, {"id": 2, "text": "same"}, {"id": 3, "text": "other"}]
    out = dedupe_messages(msgs)
    assert [m["id"] for m in out] == [1, 3]


def test_near_duplicates_kept():
    msgs = [{"id": 1, "text": "hello"}, {"id": 2, "text": "hello!"}]
    assert len(dedupe_messages(msgs)) == 2


def test_media_only_messages_are_not_deduped():
    msgs = [{"id": 1, "media": "photo"}, {"id": 2, "media": "photo"}]
    assert len(dedupe_messages(msgs)) == 2


# --------------------------------------------------------------------------
# --min-chars
# --------------------------------------------------------------------------
def test_min_chars_drops_short_text_only():
    msgs = [{"id": 1, "text": "hi"}, {"id": 2, "text": "this is long enough"}, {"id": 3, "text": "ok", "media": "photo"}]
    out = filter_min_chars(msgs, 5)
    assert [m["id"] for m in out] == [2, 3]  # media message kept regardless of caption length


def test_min_chars_counts_characters_not_bytes():
    # 4 Devanagari characters == 12 UTF-8 bytes; must be counted as 4 chars.
    msg = {"id": 1, "text": "नमस्ते"[:4]}  # 'नमस्त' -> 4 chars
    assert len(msg["text"]) == 4
    assert filter_min_chars([msg], 5) == []      # 4 < 5 -> dropped
    assert filter_min_chars([msg], 4) == [msg]   # 4 >= 4 -> kept


def test_min_chars_zero_is_noop():
    msgs = [{"id": 1, "text": "x"}]
    assert filter_min_chars(msgs, 0) == msgs
