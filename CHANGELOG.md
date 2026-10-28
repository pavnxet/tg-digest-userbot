# Changelog

## 2.0.1 — PR #1 audit fixes

Every finding from the independent audit in PR #1, fixed with a test each.

**High**

- `--per-channel-files` no longer loses channels: a name with no ASCII (any
  Devanagari title) used to slug to the literal `channel`, so two such channels
  produced one file. Non-ASCII names now get a short stable suffix, and a
  same-run name collision falls back to `_2` instead of overwriting.
- A run where **no channel could be read** now exits `1` and leaves
  `state.json` alone, so `--since-last-run` retries that window instead of
  skipping it silently.
- `autodelete_minutes` now actually deletes the digest messages it sent
  (it only logged "would be removed").
- Resume buttons re-run the same channels: the button snapshots the channels
  file, so a later `/history` for another channel can no longer change what an
  older Resume button scrapes.

**Medium**

- `/add @news | label` wrote the label twice (`@news | label | label`).
- `/find` now reads `.jsonl` (one record per message) and `.txt` digests, not
  just `.json`.
- `/preview` no longer consumes the 5-minute gap or the 12-per-day quota.
- `/status`, `/channels` and `/add` no longer disconnect the user client while a
  login is running (they used to abort the login).
- `--resume` reuses the window the interrupted run started with, instead of
  re-resolving a relative window against "now" and splicing two periods together.

## 2.0.0 — the "control panel" release

Everything below is owner-only, private-chat only, and the user client stays
strictly read-only throughout.

### New

- **Full-history scraping.** `--all-history` on the CLI, the `all` window for
  `/run`, and `/history <#|@name|link>` in the bot. No per-channel message cap,
  a 2-hour timeout instead of 30 minutes, a longer FloodWait tolerance, and the
  digest is **not** sent to the chat unless you add `send:yes`.
- **A real help system.** `/help` is a compact, grouped command list rendered as
  rich text; `/help <command>` — or tapping a command in the Telegram menu —
  shows that command's full guide (usage, every option, examples, limits).
  Only help messages use HTML; everything else stays plain text so dynamic
  content can never be mis-read as markup.
- **Resume button.** A stopped, timed-out or failed run's summary carries a
  🔁 Resume button that continues it from its checkpoint with the same options.
- **Per-file actions.** `/files` gives every output a resend button *and* a
  delete button (delete asks first and refuses any path outside the output folder).
- **Channel filters and titles.** `/channels paused` and `/channels invalid`;
  unnamed channels show their resolved title, cached in
  `.cache/channel_titles.json`.
- **`/clean` that explains itself.** Previews the exact files, sizes and total
  before confirming; supports `/clean 3d` and `/clean all`; reports space freed.
- **`/schedule list` with detail.** Scope, paused state, the next run with a
  countdown, and the last fire date.
- **`/health` repeat failures.** Channels that keep failing across the last ten
  runs, with the reason.

### Fixed — audit pass (18 findings)

Correctness and safety:

- `/channels` pagination was global state; it is now per message (the page and
  tag travel inside the button) and edits the message in place.
- Inline buttons carried live indexes. They now carry short tokens bound to the
  item they were created for (a channel line, a file path, a filter) and expire,
  so an old button can never act on a different channel or file.
- `/remove` re-resolves the entry by its raw line, so a shifted index cannot
  delete the wrong channel.
- "add anyway" keeps the resolved numeric id, so id-dedupe still works.
- `/add` no longer claims you are a member of a public channel it merely resolved.
- Duplicate schedule times are rejected instead of colliding with the 5-minute gap.
- `owner.json` is written atomically, and an unreadable file **fails closed** —
  no silent re-pairing.
- Unknown or stale button presses get a reply instead of silence.
- `/claim` has a **per-sender** attempt cap, so pairing cannot be brute-forced
  and a stranger cannot lock the real owner out.

Clarity:

- `/settings` shows what each key does and the values it accepts; `/set` with an
  unknown key lists the valid keys.
- `/pause`, `/resume`, `/tag`, `/untag` say which channel changed.
- Dead ends name the next step (`/channels` → `/add`, `/files` → `/run`, …).
- The run summary says which folder the files are in and that `/last` fetches
  them when they were not sent.
- A mistyped command suggests the closest real one.

### Fixed — bug hunt

- **The bot no longer touches the user session while a scrape is running.**
  `/channels` title lookup and `/add` verification now skip the session when a
  run is active, so they cannot fight the scraper for the SQLite session.
- `/history` refuses a **paused** channel up front and tells you how to resume it,
  instead of starting a run that silently does nothing.
- `/clean 0` is rejected — "0 days" would mean everything, which `/clean all`
  says explicitly.
- Token maps (`_picks`, `_files`, `_resume`, claim failures) are pruned and
  capped instead of growing forever.

### Tooling and docs

- `pyproject.toml`, `Makefile`, `.editorconfig`, ruff/mypy/pytest config.
- CI on Python 3.10 / 3.11 / 3.12 (ruff, mypy, bandit, pytest).
- `deploy/systemd/tg-digest-bot.service`, `deploy/tmux_run.sh`, `docs/TERMUX.md`.
- Log rotation (5 MB × 3) with the run id on every line; atomic `state.json`.
- Stricter `.env` validation with messages that point at the right line.
- A Claude-styled documentation site on GitHub Pages, plus README and
  `docs/SECURITY.md` updates.

### Known limitations

- No live Telegram testing is performed by the project; the live smoke test is
  yours to run.
- On Windows the `chmod 600` on the owner and session files is a no-op.
- If an embedded prompt alone exceeds `--max-chars`, the splitter's floor means
  parts cannot all stay under the cap.
