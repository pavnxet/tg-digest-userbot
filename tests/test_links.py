"""link_base construction for public and private chats."""
from formatter import build_link_base


def test_public_username_link_base():
    assert build_link_base(1234567890, "chname") == "https://t.me/chname/"
    assert build_link_base(1, "@chname") == "https://t.me/chname/"


def test_private_link_base_strips_100_prefix():
    assert build_link_base(-1001234567890, None) == "https://t.me/c/1234567890/"


def test_private_link_base_without_100_prefix():
    assert build_link_base(-123456789, None) == "https://t.me/c/123456789/"


def test_private_link_base_with_positive_channel_id():
    # A resolved Channel entity exposes the bare id (no -100), which is exactly
    # what t.me/c/<id>/ expects.
    assert build_link_base(1234567890, None) == "https://t.me/c/1234567890/"


def test_message_link_is_base_plus_id():
    base = build_link_base(1234567890, "chname")
    assert base + "42" == "https://t.me/chname/42"
