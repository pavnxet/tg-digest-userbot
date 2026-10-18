"""Phase 2: AST scan proving the code is read-only and join-gated.

This is a hard safety gate, not a style check. It also distinguishes the two
clients: the BOT client (bot.py / bot_handlers.py) may send and delete, but the
USER client (user_session.py and every scraper module) must not -- and within
user_session.py every call on the user client must be in a strict allowlist.

Any violation is a Critical bug that could endanger the account.
"""
from __future__ import annotations

import ast
from pathlib import Path

SCRAPER_MODULES = [
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
    "owner.py",
    "login_state.py",
]

# Files allowed to send/delete: the bot client lives here.
BOT_MODULES = ["bot.py", "bot_handlers.py"]

# Files that must never contain a forbidden call, and are scanned against the
# user-client allowlist.
USER_MODULES = ["user_session.py"]

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

# The receiver that holds the USER client inside user_session.py.
USER_CLIENT_RECEIVERS = {"self._client", "self._user_client"}

# The only methods user_session.py may call on the user client. log_out() is the
# single non-read call, allowed only here (used by /logout).
USER_CLIENT_METHODS = frozenset({
    "connect",
    "disconnect",
    "is_user_authorized",
    "get_me",
    "qr_login",
    "send_code_request",
    "sign_in",
    "log_out",
})

ROOT = Path(__file__).resolve().parent.parent


def _call_info(node: ast.Call) -> tuple[str | None, str]:
    func = node.func
    if isinstance(func, ast.Attribute):
        try:
            receiver = ast.unparse(func.value)
        except Exception:  # pragma: no cover - ast.unparse is available on 3.9+
            receiver = ""
        return func.attr, receiver
    if isinstance(func, ast.Name):
        return func.id, ""
    return None, ""


def scan_sources(sources: dict[str, str]) -> list[str]:
    """Return a list of safety violations across the given {filename: source}."""
    violations: list[str] = []
    for name, source in sources.items():
        tree = ast.parse(source, filename=name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            method, receiver = _call_info(node)
            if method is None:
                continue
            # Rule A: forbidden calls only in the bot modules.
            if method in FORBIDDEN_CALL_NAMES and name not in BOT_MODULES:
                violations.append(f"{name}: forbidden {method}() on {receiver or '?'}")
            # Rule B: within the user-client module, only allowlisted calls.
            if name in USER_MODULES and receiver in USER_CLIENT_RECEIVERS and method not in USER_CLIENT_METHODS:
                violations.append(f"{name}: {receiver}.{method}() is not in the user-client allowlist")
            # Rule C: log_out only via the wrapper in user_session.py.
            if method == "log_out" and name not in USER_MODULES and receiver in USER_CLIENT_RECEIVERS:
                violations.append(f"{name}: {receiver}.log_out() outside user_session.py")
    return violations


def _real_sources() -> dict[str, str]:
    names = SCRAPER_MODULES + USER_MODULES + BOT_MODULES
    return {name: (ROOT / name).read_text(encoding="utf-8") for name in names}


def _module_ast(name: str) -> ast.Module:
    return ast.parse((ROOT / name).read_text(encoding="utf-8"), filename=name)


# --------------------------------------------------------------------------
# The real codebase is clean
# --------------------------------------------------------------------------
def test_no_safety_violations_in_the_real_code():
    assert scan_sources(_real_sources()) == []


def test_bot_modules_are_allowed_to_send_and_delete():
    # Sanity: the bot really does send/delete, and the scanner permits it there.
    assert scan_sources({"bot_handlers.py": "async def f(bot):\n    await bot.send_file(1, b'x')\n"}) == []


def test_user_session_calls_only_the_allowlist():
    source = (ROOT / "user_session.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            method, receiver = _call_info(node)
            if receiver in USER_CLIENT_RECEIVERS:
                assert method in USER_CLIENT_METHODS, f"{method} not allowlisted"


# --------------------------------------------------------------------------
# The scanner actually catches injected violations
# --------------------------------------------------------------------------
def test_injected_send_on_user_client_is_caught():
    bad = {"user_session.py": "class S:\n    async def f(self):\n        await self._client.send_message('x')\n"}
    violations = scan_sources(bad)
    assert violations and "send_message" in violations[0]


def test_injected_delete_on_user_client_is_caught():
    bad = {"user_session.py": "class S:\n    async def f(self):\n        await self._client.delete_messages(1, [2])\n"}
    assert scan_sources(bad)


def test_injected_send_in_scraper_module_is_caught():
    bad = {"scraper.py": "async def f(client):\n    await client.send_message('x')\n"}
    assert scan_sources(bad)


def test_log_out_injected_outside_user_session_is_caught():
    bad = {"main.py": "async def f(self):\n    await self._client.log_out()\n"}
    assert scan_sources(bad)


# --------------------------------------------------------------------------
# Invite / join gating (unchanged rules)
# --------------------------------------------------------------------------
def test_import_chat_invite_only_used_in_channels_module():
    users = [name for name in SCRAPER_MODULES if "ImportChatInviteRequest" in (ROOT / name).read_text()]
    assert users == ["channels.py"], f"ImportChatInviteRequest referenced in {users}"


def test_import_chat_invite_is_auto_join_gated():
    tree = _module_ast("channels.py")
    assert "ImportChatInviteRequest" in ast.dump(tree)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            dumped = ast.dump(node)
            if "ImportChatInviteRequest" in dumped:
                assert "auto_join" in dumped, "ImportChatInviteRequest is not gated by auto_join"


def test_join_channel_request_never_used():
    for name in SCRAPER_MODULES + USER_MODULES + BOT_MODULES:
        assert "JoinChannelRequest" not in (ROOT / name).read_text(), f"JoinChannelRequest in {name}"
