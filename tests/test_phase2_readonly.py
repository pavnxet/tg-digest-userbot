"""Phase 2: AST scan proving the code is read-only and join-gated.

This is a hard safety gate, not a style check. It distinguishes the two clients:

* the BOT client (bot.py and the bot_* modules) may send / edit / delete;
* the USER client (user_session.py and every scraper/helper module) must not,
  and may only be touched through the wrapper in user_session.py.

It also matches request *classes* passed to a client, and fails on any
``shell=True`` or ``os.system`` in the project.
"""
from __future__ import annotations

import ast
from pathlib import Path

# Modules that must never contain a forbidden call.
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
    "run_manager.py",
    "channel_store.py",
    "settings_store.py",
    "schedule_store.py",
    "files_store.py",
    "run_args.py",
]

# Files allowed to send/edit/delete: the bot client lives here.
BOT_MODULES = [
    "bot.py",
    "bot_handlers.py",
    "bot_channels.py",
    "bot_run.py",
    "bot_schedule.py",
    "bot_settings.py",
    "bot_files.py",
    "bot_health.py",
]

# The wrapper is the only place the user client is touched.
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
}

# Request classes that must never appear anywhere.
FORBIDDEN_REQUEST_CLASSES = {"JoinChannelRequest"}
# Request class allowed only in channels.py, and only under --auto-join.
GATED_REQUEST_CLASSES = {"ImportChatInviteRequest": "channels.py"}

# The receivers that hold the USER client inside user_session.py.
USER_CLIENT_RECEIVERS = {"self._client", "self._user_client"}
# The only methods the wrapper may call on the user client.
USER_CLIENT_METHODS = frozenset({
    "connect", "disconnect", "is_user_authorized", "get_me", "get_entity",
    "qr_login", "send_code_request", "sign_in", "log_out",
})
# The only request classes the wrapper may pass to the user client (all read-only).
USER_CLIENT_REQUESTS = frozenset({"CheckChatInviteRequest"})

_SHELL_FUNCS = {"run", "Popen", "call", "check_call", "check_output"}

ROOT = Path(__file__).resolve().parent.parent


def _call_info(node: ast.Call) -> tuple[str | None, str]:
    func = node.func
    if isinstance(func, ast.Attribute):
        try:
            receiver = ast.unparse(func.value)
        except Exception:  # pragma: no cover
            receiver = ""
        return func.attr, receiver
    if isinstance(func, ast.Name):
        return func.id, ""
    return None, ""


def _request_class(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
    return name if name and name.endswith("Request") else None


def _is_user_client_call(method: str | None, receiver: str) -> bool:
    if receiver in USER_CLIENT_RECEIVERS:
        return True
    return receiver == "self" and method in ("_client", "_user_client")


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
            # A: forbidden calls only in the bot modules.
            if method in FORBIDDEN_CALL_NAMES and name not in BOT_MODULES:
                violations.append(f"{name}: forbidden {method}() on {receiver or '?'}")
            # B: within the wrapper, only allowlisted methods on the user client.
            if name in USER_MODULES and receiver in USER_CLIENT_RECEIVERS and method not in USER_CLIENT_METHODS:
                violations.append(f"{name}: {receiver}.{method}() is not in the user-client allowlist")
            # C: the user client is touched only in the wrapper.
            if name not in USER_MODULES and _is_user_client_call(method, receiver):
                violations.append(f"{name}: the user client is touched outside user_session.py")
            if method == "log_out" and name not in USER_MODULES and receiver in USER_CLIENT_RECEIVERS:
                violations.append(f"{name}: {receiver}.log_out() outside user_session.py")
            # D: no shell, ever.
            if method == "system" and receiver == "os":
                violations.append(f"{name}: os.system() is not allowed")
            if method in _SHELL_FUNCS:
                for kw in node.keywords:
                    if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        violations.append(f"{name}: subprocess with shell=True")
            # E: request classes (as the callee or as an argument).
            request_classes = [c for c in ([_request_class(node)] + [_request_class(a) for a in node.args]) if c]
            for request in request_classes:
                if request in FORBIDDEN_REQUEST_CLASSES:
                    violations.append(f"{name}: {request}() is never allowed")
                gated = GATED_REQUEST_CLASSES.get(request)
                if gated is not None and name != gated:
                    violations.append(f"{name}: {request}() only allowed in {gated}")
            # F: requests passed to the user client must be allowlisted.
            if _is_user_client_call(method, receiver):
                for arg in node.args:
                    request = _request_class(arg)
                    if request and request not in USER_CLIENT_REQUESTS:
                        violations.append(f"{name}: user client called with {request}()")
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
    assert scan_sources({"bot_handlers.py": "async def f(bot):\n    await bot.send_file(1, b'x')\n"}) == []


def test_user_session_calls_only_the_allowlist():
    for node in ast.walk(ast.parse((ROOT / "user_session.py").read_text(encoding="utf-8"))):
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
    assert violations and any("send_message" in v for v in violations)


def test_injected_send_inside_a_bot_module_is_still_caught():
    bad = {"bot_channels.py": "async def f(self):\n    await self._client.send_message('x')\n"}
    assert scan_sources(bad)


def test_injected_delete_on_user_client_is_caught():
    bad = {"user_session.py": "class S:\n    async def f(self):\n        await self._client.delete_messages(1, [2])\n"}
    assert scan_sources(bad)


def test_injected_mark_read_is_caught():
    bad = {"scraper.py": "async def f(client):\n    await client.send_read_acknowledge(1)\n"}
    assert scan_sources(bad)


def test_injected_import_chat_invite_outside_channels_is_caught():
    bad = {"scraper.py": "async def f(client):\n    await client(ImportChatInviteRequest('h'))\n"}
    assert scan_sources(bad)


def test_injected_join_channel_request_is_caught():
    bad = {"channels.py": "async def f(client):\n    await client(JoinChannelRequest('c'))\n"}
    assert scan_sources(bad)


def test_injected_shell_is_caught():
    assert scan_sources({"bot.py": "import os\nos.system('rm -rf /')\n"})
    assert scan_sources({"bot.py": "import subprocess\nsubprocess.run('x', shell=True)\n"})


def test_injected_user_client_request_not_allowlisted():
    bad = {"user_session.py": "class S:\n    async def f(self):\n        await self._client(SendMessageRequest(1, 'x'))\n"}
    assert scan_sources(bad)


# --------------------------------------------------------------------------
# Invite / join gating
# --------------------------------------------------------------------------
def test_import_chat_invite_only_used_in_channels_module():
    users = [n for n in SCRAPER_MODULES + USER_MODULES + BOT_MODULES if "ImportChatInviteRequest" in (ROOT / n).read_text()]
    assert users == ["channels.py"], f"ImportChatInviteRequest referenced in {users}"


def test_import_chat_invite_is_auto_join_gated():
    for node in ast.walk(_module_ast("channels.py")):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            dumped = ast.dump(node)
            if "ImportChatInviteRequest" in dumped:
                assert "auto_join" in dumped, "ImportChatInviteRequest is not gated by auto_join"


def test_join_channel_request_never_used():
    for name in SCRAPER_MODULES + USER_MODULES + BOT_MODULES:
        assert "JoinChannelRequest" not in (ROOT / name).read_text(), f"JoinChannelRequest in {name}"
