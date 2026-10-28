# BUG_REPORT.md — tg-digest-userbot

**Independent bug audit · 2026-10-27 · branch `arena/7e262944-tg-digest-userbot` (from `5883fb5`)**

17 defects found and reproduced. Ten of them have an executable reproduction in
[`tests/test_reported_bugs.py`](tests/test_reported_bugs.py) (BUG-09 needs a
wall-clock gap and is demonstrated with a measured two-run transcript instead);
each test is `@pytest.mark.xfail(strict=True)`, so the suite stays green today
and **turns into an XPASS failure the moment the bug is fixed** (the signal to
drop the marker). This is the same convention `tests/BUGS.md` used for the v1 QA
pass.

Everything below was verified against the code as it is, not from reading the
docs alone. No live Telegram account was used — all reproduction is offline
(fake Telethon client, fake bot, temporary directories).

---

## 1. Summary

| ID | Sev | Area | Bug | Repro test |
|---|---|---|---|---|
| BUG-01 | **High** | `main.py` / `write_outputs` | `--per-channel-files` silently overwrites channels, losing whole channels | `test_per_channel_files_do_not_overwrite_each_other` |
| BUG-02 | **High** | `bot_run.py` / `RunManager` | A run where every channel failed is reported as **success** and still advances `state.json`, so `--since-last-run` permanently skips that window | `test_a_run_where_every_channel_failed_does_not_advance_since_last_run` |
| BUG-03 | **High** | `bot_run.py` | `autodelete_minutes` is a documented privacy feature that **deletes nothing** | `test_autodelete_removes_the_digest_it_sent` |
| BUG-04 | **High** | `bot_run.py` | A Resume button can resume the wrong channel (shared, overwritten `*.txt` passed to `main.py`) | `test_history_resume_points_at_the_channel_it_was_created_for` |
| BUG-05 | Medium | `channel_store.py` | `/add <link> \| label` writes the label **twice** (`@news \| Daily News \| Daily News`) | `test_add_with_a_label_stores_the_label_once`, `test_add_with_an_off_marker_writes_it_once` |
| BUG-06 | Medium | `files_store.py` | `/find` never matches `.jsonl` (or `.txt`) digests — always "not found" | `test_find_searches_jsonl_digests` |
| BUG-07 | Medium | `bot_run.py` / `run_manager.py` | `/preview` (a dry run) consumes the 5-minute run slot, the 12/day quota and the "last run" record | `test_preview_does_not_use_up_the_run_slot` |
| BUG-08 | Medium | `bot_channels.py` / `bot_health.py` / `user_session.py` | Any command that touches the user client during a login **aborts that login** | `test_browsing_channels_does_not_abort_a_login_in_progress` |
| BUG-09 | Medium | `main.py` | `--resume` re-resolves the relative time window, so one digest mixes two different windows | see BUG-09 below |
| BUG-10 | Low | `channel_store.py` | Editing a line silently drops tags when it has two `tags=` segments (two parsers disagree) | `test_channel_edits_keep_every_tag` |
| BUG-11 | Low | `files_store.py` | `/files` over-counts messages in `.jsonl` digests by one (header line) | `test_files_panel_counts_jsonl_messages` |
| BUG-12 | Low | `channel_store.py` | `remember_id()` is never called → the resolved-id duplicate cache is dead code | see BUG-12 below |
| BUG-13 | Low | `splitter.py` | `split_digest` can emit a part **larger** than `--max-chars`, contradicting its docstring | see BUG-13 below |
| BUG-14 | Low | `bot_run.py` | `/last` resends only one file of a `--per-channel-files` digest | see BUG-14 below |
| BUG-15 | Low | `bot_files.py` | `/find <kw> today` searches the last 24 h, not since midnight | see BUG-15 below |
| BUG-16 | Low | `main.py` | Negative `--max-per-channel` silently truncates every channel to zero messages; `--max-chars 0` aborts only *after* the whole scrape | see BUG-16 below |
| BUG-17 | Low | `bot_handlers.py` | `QR_MAX_SENDS` is defined and never used (dead constant) | see BUG-17 below |

Severity scale used here:

* **High** — silent wrong output, silent data loss, or a documented
  privacy/safety behaviour that does not happen, with no error shown.
* **Medium** — a documented feature is broken or misleading, or wrong output in
  a narrower flow; a workaround usually exists.
* **Low** — edge case, cosmetic, or dead code.

**No account-endangering (Critical) bug was found.** The read-only guarantee
holds: the user client still only calls the allowlisted methods, and
`tests/test_phase2_readonly.py` passes.

---

## 2. Method

1. Read the 32 top-level modules (`6,555` lines) plus the README, `bot_help.py`,
   `docs/` and `tests/BUGS.md` for stated contracts.
2. Ran the existing suite: **677 passed** (no failures, no xfails) — so every
   bug below is a gap the current tests do not cover.
3. Ran the project's own static gates: `ruff check .` → clean,
   `bandit -q -r . -x ./tests` → clean. Neither catches any of the findings.
4. Wrote a repro per finding, ran it red (`--runxfail`) to confirm the exact
   failing assertion, then marked it `xfail(strict=True)`.

```
$ python -m pytest tests/test_reported_bugs.py -q            # 11 xfailed
$ python -m pytest tests/test_reported_bugs.py --runxfail -q  # 11 failed
```

---

## 3. Findings

### BUG-01 — `--per-channel-files` silently overwrites channels · **High**

**Where:** `main.py:108` (`_safe_name`), `main.py:171` (`emit` in `write_outputs`)

`write_outputs` builds each per-channel filename from
`_safe_name(channel["name"])`, which keeps only `[A-Za-z0-9_-]`, truncates to 40
characters and falls back to the literal string `"channel"` when nothing
survives. Two channels therefore collide whenever:

* their titles are **entirely non-ASCII** — every Devanagari, Arabic, Cyrillic,
  CJK or emoji title becomes `channel`;
* their titles are identical (two "News" subscriptions);
* their titles share the same first 40 usable characters.

`emit()` then does `path.write_text(...)`, so the later channel silently
replaces the earlier file. Nothing is logged, and `write_outputs` still reports
**both** paths — the same name twice.

**Evidence** (two Hindi-titled channels, `--per-channel-files`):

```
files reported written: ['..._channel.json', '..._channel.json']
files actually on disk  : ['..._channel.json']
   ..._channel.json -> ['नमस्ते']          <-- 'समाचार' gone
```

`meta.channels_ok` says 2; the digest on disk contains 1 channel. The CLI prints
`wrote ..._channel.json` twice.

**Why it matters:** silent, unrecoverable loss of a whole channel's text in a
documented output mode (`README.md:395`). This project is Hindi-first, so the
non-ASCII case is the *normal* case for its target user.

**Suggested fix:** make the name unique per run — keep a `seen` set in
`write_outputs` and append `_2`, `_3`, …; keep an ASCII fallback but include the
channel index (`..._02_channel.json`); and de-duplicate the reported paths.
Ideally transliterate/allow Unicode letters (`\w`) instead of only `[A-Za-z0-9_-]`.

---

### BUG-02 — a totally failed run is a "success" and burns the `--since-last-run` window · **High**

**Where:** `main.py:483` (`state_mod.set_last_run(STATE_PATH, window.until, tz)`),
`run_manager.py:275` (result derived from exit code only)

`main.run` writes `state.last_run` unconditionally after `write_outputs`, even
when every channel was skipped. Because `RunManager` maps `exit_code == 0` to
`RESULT_OK`, the bot then shows a green ✅ for a run that collected nothing.

**Evidence** (single channel that cannot be resolved):

```
channels_ok=0  messages_total=0  skipped=1  parts=1
exit code: 0
state.json: {"last_run_utc": "2026-10-27T18:38:19.514296+00:00", ...}
```

**Why it matters:** with `--since-last-run` / `/run since-last`, the *next* run
starts at the failed run's `until`. Every message in the failed window is then
skipped **forever**, with no warning — the digest looks complete and is not.
With `--per-channel-files` the same run writes **zero** files and still reports
success.

**Suggested fix:** only advance the watermark when at least one channel
succeeded (`if results:`), and return a non-zero exit code (or a distinct
`RESULT_PARTIAL`) when `channels_ok == 0` so the bot does not claim success.

---

### BUG-03 — `autodelete_minutes` deletes nothing · **High**

**Where:** `bot_run.py:311-320` (`_maybe_autodelete`)

```python
async def _later() -> None:
    await asyncio.sleep(minutes * 60)
    _log.info("autodelete: %s digest messages would be removed", len(self._last_outputs))
```

It logs the *intent* and stops. `delete_messages` is never called;
`self._autodelete_task` exists only to be cancelled.

**Evidence:** with `autodelete_minutes=1` and a sent digest, after the timer
fires the fake bot's `deleted` list is still empty.

**Why it matters:** this is advertised as a privacy/hygiene control in four
places — `bot_help.py:299`, `settings_store.py:71` ("delete my sent digests
after N minutes"), `README.md:268`, `docs/index.html:815`, and
`docs/SECURITY.md:128` ("`autodelete_minutes` can remove the…"). A user who
sets it believes their scraped channel text is being removed from Telegram. It
never is. Silent privacy failure.

**Suggested fix:** keep the returned message ids from `_send_outputs` and call
`self.app.bot.delete_messages(chat_id, ids)` in `_later()`, guarded by
`contextlib.suppress`. Also cancel/replace the task when a new digest is sent so
two timers do not fight.

---

### BUG-04 — a Resume button can resume a *different* channel · **High**

**Where:** `bot_run.py:153` (`history_channel.txt`), `bot_run.py:82`
(`run_channels.txt`), `bot_run.py:245` (the token stores the argv)

Every `/history` run writes the single channel to the **same fixed path**
`<cache>/history_channel.txt`, and every `group:`/`channel:` run writes
`<cache>/run_channels.txt`. The Resume token stores the *argv*, which contains
that path — not the channel. Starting a second history/subset run overwrites the
file, so pressing the earlier Resume button resumes run A's checkpoint while
querying run B's channels.

**Evidence:**

```
run1 argv: [... '--channels-file', '/tmp/.../cache/history_channel.txt']
file content: @channelA
file content now: @channelB
resume argv: [... '--channels-file', '/tmp/.../cache/history_channel.txt']
=> resume would scrape: @channelB   (the token was created for @channelA)
```

**Why it matters:** the resulting digest mixes two unrelated channels under one
`meta`, and the owner sees no error. It also contradicts the file's own stated
design ("inline buttons carry short tokens bound to the item they were created
for, so an old button can never act on a different channel or file" —
`CHANGELOG.md`).

**Suggested fix:** write the subset to a per-run unique file
(`history_channel_<token>.txt`) and let the token carry that path; delete the
file when the token expires.

---

### BUG-05 — `/add <link> | label` duplicates the label · **Medium**

**Where:** `channel_store.py:262`

```python
lines.append(render_line(spec.raw, label or spec.label, list(tags or spec.tags), spec.paused))
```

`spec.raw` is the **whole original line**, including every `| label`, `| tags=…`
and `| off` segment already parsed out of it. `render_line` then appends those
segments again.

**Evidence:**

| Input | Line written to `channels.txt` | Parsed label |
|---|---|---|
| `/add @news \| Daily News` | `@news \| Daily News \| Daily News` | `Daily News\|Daily News` |
| `/add @news \| off` | `@news \| off \| off` | — |

**Why it matters:** the syntax is documented in three places — the usage line in
`cmd_add`, `bot_help.py:134-149` (`/add @news | Daily News`), and
`channels.txt.example:3,10`. The stored label is corrupted immediately and is what
`/channels` displays. Repeated edits keep the corrupted label.

**Suggested fix:** pass the canonical entry, not the raw line:
`render_line(spec.value, label or spec.label, ...)`
(or `spec.raw.split("|")[0].strip()`).

---

### BUG-06 — `/find` never searches `.jsonl` (or `.txt`) digests · **Medium**

**Where:** `files_store.py:119-122`

`find()` explicitly allows `.json` **and** `.jsonl`, then calls
`json.loads()` on the whole file. A `.jsonl` file is not valid JSON (one object
per line), so `json.loads` raises `ValueError`, which the `except` swallows:
every `.jsonl` digest is silently skipped. `.txt` digests are not in the suffix
list at all, so they are never searched either.

**Evidence:** a directory containing only
`digest_2026-10-27_0900.jsonl` with the text `"admit card released"`:

```
find('admit') -> []        # expected 1 hit
```

**Why it matters:** `/find` answers "कहीं नहीं मिला" — a confident false negative
— for any user who sets `format` to `jsonl` (a documented setting, and the
format `README.md:456` recommends for large exports) or `txt`.

**Suggested fix:** branch on `info.path.suffix`: parse JSONL line by line, and
either implement a plain-text scan for `.txt` or state clearly in `/help` that
only JSON/JSONL are searched.

---

### BUG-07 — `/preview` consumes the run quota · **Medium**

**Where:** `bot_run.py` `cmd_preview` → `run_manager.py:275` (`_history.record`),
`run_manager.py:193` (`check_limits`)

`cmd_preview` calls `rm.start([... , "--dry-run"], capture=True)` and then
`rm.wait()`. `wait()` records the dry run in `RunHistory` exactly like a real
run, so:

* `check_limits()` refuses the next `/run` for 5 minutes;
* the dry run counts toward `RUN_MAX_PER_DAY` (12/day);
* `/health` reports the preview as "last run: ok".

**Evidence:**

```
preview result: ok | exit: 0
check_limits right after a preview: एक run के बाद कम से कम 5 मिनट रुकें (299s बाकी)।'
history entries: [{'result': 'ok', 'duration': 0.009, ...}]
```

**Why it matters:** the natural workflow — *preview, then run* — is blocked for
5 minutes, and `/preview` itself is documented (`bot_help.py:240`) as a cheap,
side-effect-free check.

**Suggested fix:** add a `dry_run: bool` argument to `RunManager.start/wait` and
skip `_history.record()` (and the min-interval check) for it.

---

### BUG-08 — an unrelated command aborts a login in progress · **Medium**

**Where:** `bot_channels.py:117/127` (`_refresh_titles`),
`bot_channels.py:190/211` (`_resolve_add`), `bot_handlers.py` `cmd_status`,
`bot_health.py:43/49` (`cmd_health`), `user_session.py` (one shared `_client`)

`UserSession` holds **one** client for the whole process. `_refresh_titles`,
`_resolve_add` and `cmd_status` deliberately connect and then unconditionally
`await self.app.user.disconnect()` in a `finally` — they guard against a running
*scrape* (`app.run_active`) but not against an active *login*
(`app.manager.active`). `/login`, `/login_phone` and `/logout` check
`manager.active`; these three (and `/health`) do not. Disconnecting sets
`UserSession._client = None`, so the next step of the login fails.

**Evidence** — a login flow started with `/login_phone`, then one unrelated
command, then the phone number:

```
command            client alive after   login broken
/status            False                 True
/channels          False                 True
/add @x            False                 True
/health            True                  False     (it reconnects itself)
/prompts           True                  False
/settings          True                  False
```

The first step of the failure:

```
after /login_phone -> active: True | awaiting: phone | client attached: True
after /channels    -> active: True | awaiting: phone | client attached: False
after phone number -> replies: ['Code request fail हो गया。']
flow active: False
```

**Why it matters:** the owner loses the login flow and has to start over (and
`/channels` is the command they are most likely to type while waiting for a
code). For QR logins the same disconnect pulls the client out from under the
pending `qr.wait()` task, which then fails or stalls until the 5-minute login
timeout.

**Suggested fix:** give `UserSession` a re-entrant lease —
`async with self.app.user.session():` — that only disconnects when the last
holder leaves, and refuse/queue it while `app.manager.active`.

---

### BUG-09 — `--resume` slides the time window · **Medium**

**Where:** `main.py:302` (`resolve_window(...)` is re-evaluated on every
invocation); the checkpoint (`Checkpoint.load_done`) stores channel payloads but
not the window.

A resumed run rebuilds the window from the *current* clock, while channels
already in the checkpoint are replayed verbatim from the first attempt. The
digest therefore mixes two windows and `meta.from`/`meta.to` describe only the
new one.

**Evidence** (`--last 2h`, channel B fails on attempt 1, resume ~70 s later):

```
run1 meta: from 2026-10-27 22:10  to 2026-10-28 00:10  channels_ok: 1  ['ChanA']
run2 meta: from 2026-10-27 22:11  to 2026-10-28 00:11  channels_ok: 2  ['ChanA', 'ChanB']
ChanA in run2 came from the checkpoint (message t unchanged: 2026-10-28 00:05)
```

Over a long `/history` resume (hours) the drift is much larger; with `--today`
across midnight the resumed part can silently become an **empty** window.

**Suggested fix:** store `window.since/until` in the checkpoint header and reuse
them on `--resume` (ignore/validate the recomputed ones, or re-read only the
delta).

---

### BUG-10 — editing a channel line silently drops tags · **Low**

**Where:** `channel_store.py:74` vs `channels.py:84-88`

The digest parser (`parse_channels_file` → `parse_channel_line`) **accumulates**
tags across multiple `tags=` segments; the editor parser
(`ChannelStore._split_line`) **replaces** them. Any line with two `tags=`
segments loses all but the last on the next edit (`/pause`, `/tag`, `/untag`),
so `group:<tag>` runs quietly stop including that channel.

```
input line : @news | tags=jobs,delhi | tags=urgent
digest sees: ('jobs', 'delhi', 'urgent')
after /pause writes: @news | tags=urgent | off   -> only ('urgent',)
```

**Suggested fix:** use one shared line parser (import `parse_channel_line` in
`channel_store`), or extend the tag list instead of assigning it.

---

### BUG-11 — `/files` over-counts `.jsonl` messages · **Low**

**Where:** `files_store.py:39`

The JSONL message count sums **every** non-blank line, but line 1 is the
`ai_instructions` + `meta` header. The panel shows `N+1 messages`.

```
_message_count("…jsonl") = 2     # file has 1 message
_message_count("…json")  = 1
```

**Fix:** subtract one for the header line (or count only lines with a `text`/`id`
key).

---

### BUG-12 — the resolved-id duplicate cache is dead code · **Low**

**Where:** `channel_store.py:125` (`remember_id`) — called from
`tests/test_channel_store.py` only.

`bot.py:111` wires `ids_path=CACHE_DIR/"channel_ids.json"`, and
`ChannelStore._duplicate` consults `self._ids`, but nothing ever writes to it:
`add()` receives `resolved_id` and never stores it. Consequences:

* `channel_ids.json` is never created;
* adding `@news` and then the same chat's numeric id `-100…` is not detected as
  a duplicate (`CHANGELOG.md` claims "add anyway keeps the resolved numeric id,
  so id-dedupe still works");
* `remember_id` is untested in production paths.

Impact is limited: `main.py` de-duplicates by resolved entity id at run time, so
a repeated channel does not double up in the digest — it just stays listed twice
in `channels.txt` (and is scraped twice).

**Fix:** call `self.remember_id(self._key(spec), resolved_id)` at the end of a
successful `add()` (and in `_do_add_anyway`).

---

### BUG-13 — `split_digest` can exceed `--max-chars` · **Low**

**Where:** `splitter.py:33-56`

The docstring promises "one or more complete digest objects, **each under
`max_chars`**". `_split_channel` never splits a single message, so one message
larger than the budget produces an oversized part — and `budget` is floored at
`_MIN_BUDGET` (1000) regardless of `max_chars`.

```
max_chars=2000, one 5100-char message -> one part of 5159 chars
```

Reachable with `--max-chars` below ~4 500 (Telegram messages are capped at 4096
characters; the bot's `/set` forbids such values, the CLI does not). Not
data-loss, but the stated invariant is false.

**Fix:** either split/truncate such a message, or raise a clear error when a
single message cannot fit; document the `_MIN_BUDGET` floor.

---

### BUG-14 — `/last` resends only one per-channel file · **Low**

**Where:** `bot_run.py:331`

```python
base = outputs[-1].name.split("_part")[0].split(".json")[0]
self._last_outputs = [p for p in outputs if p.name.startswith(base)]
```

`--per-channel-files` names are `<base>_<channel>` with no `_part` marker, so the
"group" is just the newest file. Verified: two per-channel digests on disk →
`/last` sends **1**.

**Fix:** derive the run base from the known `digest_YYYY-MM-DD_HHMM` prefix
(regex) instead of string splitting.

---

### BUG-15 — `/find <kw> today` means "last 24 h" · **Low**

**Where:** `bot_files.py:49-59` (`_window_hours`)

`today` → `24.0` hours and `yesterday` → `48.0` hours. Everywhere else in the
project `today` means "since midnight IST" (`timeutil.resolve_window`), so
`/find` disagrees with `/run today`.

**Fix:** compute the window from `datetime.now(tz)` at midnight, like
`resolve_window(today=True)`.

---

### BUG-16 — unchecked CLI caps · **Low**

* `--max-per-channel -1` is accepted; `scanned > -1` is true for the first
  message, so **every** channel is marked `truncated` with `cap: -1` and stores
  zero messages.
* `--max-chars 0` (or negative) is accepted by argparse and only raises inside
  `split_digest` — i.e. **after** the entire scrape, which then writes nothing.
  `main` catches it as "Error: max_chars must be positive" (exit 2).

**Fix:** validate both up front (`max_per_channel >= 0`, `max_chars >= 1000`)
right after `apply_mode_defaults`.

---

### BUG-17 — dead constants · **Low**

* `bot_handlers.py:50` — `QR_MAX_SENDS` is never referenced (the QR flow uses
  `QR_MAX_REFRESHES`).
* `bot_run.py:53` — `_autodelete_task` is assigned and never awaited/cancelled
  (see BUG-03).

---

## 4. Things that look wrong but are fine (checked, no action)

Recording these so the next reviewer does not re-investigate them:

* `formatter.clean()` drops `false`/`null`/empty but keeps `0` (`views: 0`,
  `count: 0`) — correct.
* `Window.contains` is genuinely half-open; `--yesterday` ends at
  `23:59:59.999999`, a sub-microsecond edge that cannot occur in Telegram data.
* `scraper.scrape_channel`'s blanket `except Exception` does **not** swallow
  `KeyboardInterrupt`/`CancelledError` (they are `BaseException`) — Ctrl-C and
  `/stop` still stop a run and keep the checkpoint.
* `owner.OwnerStore` fails **closed** on a corrupt `owner.json`;
  `secrets.compare_digest` is used for the pairing code; the per-sender attempt
  cap cannot be used to lock the real owner out.
* `files_store._safe_in_dir` correctly refuses symlinks escaping `output/` and
  anything not named `digest_*`.
* No shell is ever involved in spawning `main.py` (`argv` list, `shell=False`);
  chat text cannot inject flags (`run_args` allowlists every token).
* `ruff` and `bandit` are clean; the read-only scan
  (`tests/test_phase2_readonly.py`) still passes.

---

## 5. How to reproduce

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest

# all 11 executable repros (currently reported as xfailed)
pytest tests/test_reported_bugs.py -q

# see them actually fail, with the exact assertion for each bug
pytest tests/test_reported_bugs.py --runxfail -q

# full suite (677 original tests + 11 xfailed repros)
pytest -q
```

The seven findings without a test (BUG-09, BUG-12 … BUG-17) are demonstrated by
the snippets quoted in §3. BUG-09 needs two runs at least one minute apart,
which is why it is not in the fast suite.

---

## 6. Suggested fix order

1. **BUG-02** and **BUG-03** — silent data loss and a silent privacy failure.
   Both are a few lines.
2. **BUG-01** — make per-channel filenames unique; one digest is losing a whole
   channel.
3. **BUG-04** — per-run subset files; a wrong-channel resume corrupts a digest.
4. **BUG-05**, **BUG-06**, **BUG-07**, **BUG-08** — each is a self-contained,
   well-scoped change.
5. **BUG-09** — persist the window in the checkpoint (slightly more invasive but
   it makes `--resume` correct by construction).
6. The Low items, opportunistically, together with their regression tests.

When a fix lands, delete that test's `@pytest.mark.xfail` marker (strict xfail
turns green → XPASS failure) and add the test to `tests/BUGS.md` as a normal
regression test, following the existing v1 convention.
