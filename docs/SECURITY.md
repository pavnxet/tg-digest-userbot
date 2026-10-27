# SECURITY.md — threat model for the control bot

## Threat model

The control bot lets you log your Telegram **user** account in from a chat with a
bot you created in @BotFather. The design is built around these facts:

1. **The bot chat is NOT end-to-end encrypted.** Everything typed there is stored
   on Telegram's servers until it is deleted. So the login code and the 2FA
   password are exposed the moment you send them, even if the bot deletes them a
   second later.
2. **The code and the 2FA password are the keys to your whole Telegram account.**
   Anyone who reads them can log in as you.
3. **Only you may use the bot.** Every other user must be silently ignored.

## Secrets inventory — what they are and where they live

| Secret | Where it lives | How it is protected |
|---|---|---|
| `API_ID`, `API_HASH` | `.env` (git-ignored) | Never printed; never sent to the chat |
| `PHONE` (user account) | `.env` (optional) and, during `/login_phone`, in memory | `.env` git-ignored; chat copy deleted at once; never logged |
| Login **code** | In memory for the length of one `sign_in` call | Chat message deleted immediately; digits only, separators stripped; never logged; not stored in state |
| **2FA password** | In memory for the length of one `sign_in` call | Chat message deleted immediately; local variable only, `del`-ed right after use; never logged; never in state |
| **QR login token** (`tg://login?token=…`) | Shown to the owner in the chat (by design, so you can tap it) | Single-use and short-lived; never logged; the QR image is rendered in memory (no file) |
| `BOT_TOKEN` | `.env` (git-ignored) | Never printed; never sent to the chat |
| Owner id | `owner.json` (git-ignored, chmod 600) | Numeric id only, never a username |
| User **session** file | `tg_digest.session` (git-ignored, chmod 600) | Full account access; never sent to the chat; deleted on `/logout` |
| Bot session file | `bot.session` (git-ignored) | Bot credential only |

## Controls

- **Owner lock.** Only the numeric owner id is served. Non-owner updates are
  dropped with **no reply and no content logged**. The check runs in one place
  (`BotApp.handle` / `BotApp.handle_callback`) before any command is dispatched,
  so a new handler cannot forget it. Only private chats are handled; groups,
  channels and inline queries are ignored.
- **One-time pairing.** If `OWNER_ID` is unset, a random code is printed to the
  **terminal only**. The first user to send `/claim <code>` becomes the owner; the
  code is invalidated immediately and can never be reused. The code is never sent
  in the chat. After **5 wrong guesses** the bot stops accepting claims until it is
  restarted (a fresh code is printed then); failures are logged as a count only.
- **Owner file is atomic and fails closed.** `owner.json` is written to a temp file
  and `os.replace`d into place, so a crash cannot leave a half-written file. If the
  file exists but cannot be read, the bot does **not** fall back to pairing — it
  treats nobody as the owner, ignores every message, and prints how to fix it in the
  terminal. A damaged file can never hand the bot to someone else.
- **Buttons cannot go stale.** Inline buttons carry short tokens bound to the item
  they were created for (a channel line, a file path, a filter), never a live index,
  and expire. A button from an old message can never act on a different channel or
  file; it replies that it is out of date.
- **Destructive actions always confirm.** `/remove`, `/clean`, per-file delete,
  `/logout`, `/panic` and `/schedule remove` ask for an inline confirmation, and the
  confirmation callback passes the same owner check and expires after 60 seconds.
- **Message hygiene.** Every sensitive incoming message (phone, code, password,
  `/claim`) is deleted right after it is read. Deletion is best-effort: if it
  fails, the bot tells you to delete it manually and logs the failure **without
  the content**.
- **Minimal retention.** The login state machine holds one login at a time and
  clears all state on success, failure, `/cancel` and a 5-minute inactivity
  timeout. The password is never stored at all.
- **Read-only user client.** The user client is touched only by `user_session.py`,
  which calls a strict allowlist of methods (login, status, logout). The AST
  safety scan (`tests/test_phase2_readonly.py`) enforces this by inspecting the
  receiver of every call, and fails if a send/delete/edit call appears on the user
  client or anywhere in the scraper modules. The single non-read call is
  `log_out()`, used only by `/logout`.
- **Session locking.** The user session is an SQLite file. `bot.py` connects the
  user client only for login / status / logout and disconnects afterwards, so it
  will not fight with a running scraper. A locked session produces a clear
  message instead of a crash.
- **Files stay in their folder.** `/clean`, per-file delete and the retention job
  only ever touch the configured output folder, never follow a symlink out of it,
  and never delete `.cache/run_history.json` or `.cache/run.lock` (wiping those
  would reset the daily and minimum-gap run limits).

## What this cannot protect against

- **Telegram's servers** hold whatever you type in the bot chat until it is
  deleted. Deletion is best-effort and not guaranteed.
- **Telegram may block login codes sent through a chat.** If sign-in fails right
  after a correctly typed code, the bot says so and points you at `/login` (QR) or
  the terminal.
- **A compromised device or account** can read the chat, the `.session` file and
  `.env`.

## The safest option

For the **2FA password in particular**, the safest path is to do the whole login
**in the terminal** (`python main.py --terminal-login`): nothing is ever sent
through a chat. The bot is a convenience for QR login; treat the phone+code and
2FA steps as a fallback.

## The control panel (channels, runs, schedules, files)

The bot can now manage channels, run the scraper, schedule runs, change settings,
list/clean outputs and search them. These additions keep the same guarantees:

- **The user client stays read-only.** `user_session.py` is still the only place
  it is touched, and the allowlist is unchanged except for two read-only
  additions: `get_entity` and `CheckChatInviteRequest` (checking an invite never
  joins it). The AST safety scan (`tests/test_phase2_readonly.py`) enforces this
  by inspecting the receiver of every call, and now also fails on any
  `ImportChatInviteRequest` outside `channels.py`, any `JoinChannelRequest`, any
  request class passed to the user client that is not allowlisted, and any
  `shell=True` or `os.system` anywhere in the project.
- **No shell.** The scraper is started as a separate process with an argv
  **list** (`shell=False`). Only validated values ever reach argv; unknown tokens
  are rejected, so `--auto-join` can never be passed. A test asserts this.
- **Account protection.** One run at a time (crash-safe lock), a minimum
  5-minute gap, a maximum of 12 runs per day (manual and scheduled share the
  limit), and a 30-minute hard timeout that kills the whole process tree.
- **File edits are safe.** `channels.txt` edits rewrite only the affected line,
  keep comments/blank lines/order/line endings and UTF-8 (no BOM), write
  atomically, and keep a `channels.txt.bak`. A lock plus a "run active" check
  stop the bot and a running scraper from overlapping.
- **Retention and `/clean` stay inside the output folder.** A path is never taken
  from chat input, and a symlink whose target escapes the output folder is
  skipped.
- **`/panic` keeps the run limits.** It deletes `.cache/` **except**
  `run_history.json` and `run.lock` — wiping those would reset the daily and
  minimum-gap limits. It keeps `channels.txt` and `settings.json`, and reports
  every step (including partial failures).
- **Destructive actions** (`/remove`, `/clean`, `/logout`, `/panic`,
  `/schedule remove`) require an inline-button confirmation that also passes the
  owner check and expires after 60 seconds. `/panic` needs two.
- **Digest files sent to the chat are stored on Telegram's servers.** The bot
  deletes its own copies on `/panic` and after `retention_days`, but the copies
  in the chat remain until you delete them. `autodelete_minutes` can remove the
  bot's sent digest messages on a timer (default off).
