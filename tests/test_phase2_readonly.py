"""Phase 2: AST scan proving the code is read-only and join-gated.

This is a hard safety gate, not a style check. Any forbidden call is a Critical
bug that could endanger the account.
"""
from __future__ import annotations

import ast
from pathlib import Path

APP_MODULES = [
    "config.py",
    "timeutil.py",
    "state.py",
    "logging_setup.py",
    "formatter.py",
    "splitter.py",
    "client.py",
    "channels.py",
    "scraper.py",
    "main.py",
]

FORBIDDEN_CALL_NAMES = {
    "send_message",
    "send_file",
    "send_read_acknowledge",
    "mark_read",
    "delete_messages",
    "edit_message",
    "forward_messages",
    "send_media",
    "send_reaction",
    "get_participants",
    "iter_participants",
    "ReadHistoryRequest",
    "SendReactionRequest",
    "SendMessageRequest",
    "DeleteMessagesRequest",
    "EditMessageRequest",
    "ForwardMessagesRequest",
    "JoinChannelRequest",
}

ROOT = Path(__file__).resolve().parent.parent


def _module_ast(name: str) -> ast.Module:
    return ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)


def _called_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_no_forbidden_calls_in_app_code():
    offenders = {}
    for name in APP_MODULES:
        hits = _called_names(_module_ast(name)) & FORBIDDEN_CALL_NAMES
        if hits:
            offenders[name] = sorted(hits)
    assert not offenders, f"Forbidden (write/send/read-ack) calls found: {offenders}"


def test_import_chat_invite_only_used_in_channels_module():
    users = [name for name in APP_MODULES if "ImportChatInviteRequest" in (ROOT / name).read_text()]
    assert users == ["channels.py"], f"ImportChatInviteRequest referenced in {users}"


def _guarded_by_auto_join(tree: ast.Module) -> bool:
    """True if every ImportChatInviteRequest(...) call sits inside a function
    that also branches on an `auto_join` attribute (the --auto-join guard)."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            body_src = ast.dump(node)
            if "ImportChatInviteRequest" in body_src:
                if "auto_join" not in body_src:
                    return False
    return True


def test_import_chat_invite_is_auto_join_gated():
    tree = _module_ast("channels.py")
    # There must be at least one guarded call ...
    assert "ImportChatInviteRequest" in ast.dump(tree)
    # ... and it must live in a function that consults auto_join.
    assert _guarded_by_auto_join(tree), "ImportChatInviteRequest is not gated by auto_join"


def test_join_channel_request_never_used():
    for name in APP_MODULES:
        assert "JoinChannelRequest" not in (ROOT / name).read_text(), f"JoinChannelRequest in {name}"
