"""Phase 3: channels.txt parsing (formats, robustness, edge cases)."""
import pytest

from channels import parse_channel_line, parse_channels_file


# --------------------------------------------------------------------------
# Formats that must work
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line,kind,value",
    [
        ("@durov", "username", "durov"),
        ("https://t.me/durov", "username", "durov"),
        ("https://t.me/durov/", "username", "durov"),
        ("@Durov", "username", "Durov"),  # case preserved; Telegram is case-insensitive
        ("-1001234567890", "id", "-1001234567890"),
        ("123456", "id", "123456"),
        ("https://t.me/+AbCdEfGhIjK", "invite", "AbCdEfGhIjK"),
        ("https://t.me/joinchat/AbCdEfGhIjK", "invite", "AbCdEfGhIjK"),
    ],
)
def test_parse_ok_formats(line, kind, value):
    spec = parse_channel_line(line)
    assert spec is not None
    assert spec.kind == kind and spec.value == value


def test_comment_and_blank_lines_are_ignored():
    assert parse_channel_line("# comment") is None
    assert parse_channel_line("   # indented comment") is None
    assert parse_channel_line("") is None
    assert parse_channel_line("    ") is None


def test_label_suffix():
    spec = parse_channel_line("@ex | My Label")
    assert spec.kind == "username" and spec.value == "ex" and spec.label == "My Label"


def test_label_containing_pipe_keeps_rest():
    spec = parse_channel_line("@ex | a|b")
    assert spec.label == "a|b"


def test_crlf_file_is_parsed(tmp_path):
    path = tmp_path / "channels.txt"
    path.write_bytes(b"@a\r\n@bbbb\r\n")
    specs = parse_channels_file(path)
    assert [s.value for s in specs] == ["a", "bbbb"]


# --------------------------------------------------------------------------
# BUG-07 / BUG-08: formats the spec promises but the parser mangles
# --------------------------------------------------------------------------
def test_schemeless_t_me_url():
    spec = parse_channel_line("t.me/durov")
    assert spec.kind == "username" and spec.value == "durov"


def test_url_with_query_string_and_fragment():
    assert parse_channel_line("https://t.me/durov?start=1").value == "durov"
    assert parse_channel_line("https://t.me/durov#frag").value == "durov"


def test_post_link_resolves_to_channel():
    spec = parse_channel_line("https://t.me/durov/123")
    assert spec.kind == "username" and spec.value == "durov"


def test_private_post_link_resolves_to_channel_id():
    spec = parse_channel_line("https://t.me/c/1234567890/45")
    assert spec.kind == "id" and spec.value == "-1001234567890"


def test_utf8_bom_is_stripped(tmp_path):
    path = tmp_path / "channels.txt"
    path.write_bytes(b"\xef\xbb\xbf@durov\n@telegram\n")
    specs = parse_channels_file(path)
    assert specs[0].value == "durov" and specs[0].kind == "username"


def test_unparseable_line_becomes_invalid_not_dropped():
    spec = parse_channel_line("???")
    assert spec is not None and spec.kind == "invalid"


# --------------------------------------------------------------------------
# Duplicates: the same channel in two forms
# --------------------------------------------------------------------------
def test_duplicate_channel_in_two_forms_is_returned_twice_by_parser():
    # Documents current behaviour; the *deduplication* requirement is checked at
    # the integration level (BUG-01).
    specs = parse_channels_file(_write("https://t.me/durov\n@durov\n"))
    assert len(specs) == 2


def _write(text):
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "channels.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_parser_never_raises_on_garbage(tmp_path):
    weird = "\n".join(
        ["???", "http://", "@", "|", "| label only", "https://t.me/", "\x00null", "🚀 rocket", "@x|"]
    )
    path = tmp_path / "c.txt"
    path.write_text(weird, encoding="utf-8")
    specs = parse_channels_file(path)  # must not raise
    assert isinstance(specs, list)
