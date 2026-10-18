# TEST_PLAN.md — tg-digest-userbot (v1 QA)

Author: QA review. Scope: the whole repository at `tg-digest-userbot/`.
Method: read-only review first, then static analysis, then a layered test suite
(unit → integration with a fake Telethon client → property/fuzz → CLI → perf).
**No real Telegram account is used except in Phase 8, and only with the owner's
explicit go-ahead.**

---

## 1. Modules and responsibilities

| Module | Responsibility | Main risks |
|---|---|---|
| `config.py` | Load `.env`, paths, defaults, `ai_instructions` | Secret handling; friendly errors |
| `timeutil.py` | Duration/IST parsing, UTC window resolution | Boundary inclusivity; naive vs aware; conflicting flags |
| `channels.py` | Parse `channels.txt`, resolve entities, invite/join policy | Never join without `--auto-join`; parsing robustness; duplicates |
| `scraper.py` | Read loop + extraction + album merge + dedupe | Early break (no full history); UTF-16 links; album merge |
| `formatter.py` | Cleanup, `link_base`, empty-field stripping, formats, token estimate | `ensure_ascii=False`; schema; dropped fields |
| `splitter.py` | Split digest into standalone parts | Data loss/duplication; oversized single message |
| `state.py` | `state.json` last-run + resume run id | Updated only after a successful write |
| `logging_setup.py` | Log to `logs/run.log` | Secrets in logs; unbounded growth |
| `main.py` | argparse, orchestration, checkpointing, output | Crash-resilience; name collisions; write-only-on-success |
| `client.py` | Telethon client + login | Read-only guarantee |

## 2. Account-safety invariants (must never be violated)

1. No `send_*`, `edit_*`, `delete_*`, `forward_*`, `SendReactionRequest`,
   `ReadHistoryRequest`, `mark_read`, `send_read_acknowledge` anywhere.
2. `ImportChatInviteRequest` / `JoinChannelRequest` reachable **only** when
   `--auto-join` is set.
3. Joins are rate-limited (30–60 s) and capped (`--max-joins`).
4. No secrets in logs / stdout / committed files.
5. No tight loops; a random 1–3 s gap between channels.

## 3. Test cases, by phase

### Phase 2 — Static
- `ruff`, `mypy`, `bandit`, `pip-audit`, `python -m compileall`.
- AST scan of every module for the forbidden calls above; AST reachability check
  that `ImportChatInviteRequest` sits behind an `auto_join` guard.

### Phase 3 — Unit (target ≥85% line; 100% on time/links/splitter/formatter)
- **Time**: `--today/--yesterday/--last h,d,w/--since/--until/--since-last-run`;
  boundaries 00:00/23:59, midnight-crossing, zero-length, since>until, future,
  malformed, `--last 0h/-3d/3x`, uppercase, huge values; conflicting flags error;
  IST→UTC +05:30, no DST; awareness; start/end inclusivity vs docs.
- **channels.txt**: all formats, scheme-less, query strings, post links, CRLF,
  BOM, trailing slash, uppercase, `|` labels, duplicates, invalid lines.
- **Links**: public/private `link_base`, `-100` strip, no-username, message link.
- **Extraction**: text, caption-only, media-only, polls/quizzes, previews,
  forwards (channel/user/hidden), replies, edits, service (skip), empty (skip),
  `post_author`; hidden links on Hindi/emoji/ZWJ/combining/surrogate text;
  4096-char, whitespace-only, RTL.
- **Albums**: same `grouped_id`, caption first/later/none, media count, split
  across boundary, two adjacent albums.
- **Dedupe/min-chars**: per-channel, first kept, near-dupes kept, Hindi counted
  in characters.
- **Formatter**: JSON Schema validation; `ensure_ascii=False`; compact; no empty
  fields; `ai_instructions` first; `--summary-prompt-file` (missing/empty);
  UTF-8 no BOM; round-trip; `--pretty`; JSONL; txt.
- **Splitter**: below/at/above limit; oversized single channel; oversized single
  message; every part valid + `part "X/Y"`; no loss/duplication; naming.
- **Token estimate**: labelled "estimated"; Devanagari > Latin.
- **State/checkpoint**: updated only on success; missing/corrupt/future state;
  resume de-duplicates done channels.

### Phase 4 — Integration with `FakeTelegramClient`
Early break; oldest→newest sort; stable order on equal timestamps; empty
channel/window; member vs not, `--auto-join`, `--max-joins`, join delays
(patched sleep), `pending_approval`; `--max-per-channel` + visible truncation;
per-channel errors (private/banned/expired/invalid/flood/network/generic);
counts consistency; `--dry-run` writes nothing; `KeyboardInterrupt`; assert no
write/send/read-ack method is ever called.

### Phase 5 — Property/fuzz (`hypothesis`)
Random Unicode through extraction/formatter/splitter; random interval/limit for
the splitter (invariant: parts reconstruct the original); random channel lines
(parser never raises).

### Phase 6 — CLI/config
`--help`; unknown flag; missing `.env`/vars; missing/empty `channels.txt`;
unwritable output dir; same-minute collision; logs created + secret-free;
`.gitignore` + `git ls-files`.

### Phase 7 — Performance
50×5000 synthetic messages: runtime, `tracemalloc` peak, output size; entity
caching (no per-message lookups); delay floor across N channels.

### Phase 8 — Live smoke (owner go-ahead required)
`--dry-run`, then one joined channel, `--last 1h`, no `--auto-join`, one run;
manual verification; stop on FloodWait >60 s.

## 4. Bug-exposing tests
Where a test asserts documented/expected behaviour that the code does not
deliver, it is marked `xfail(strict=True)` with the `BUG-xx` id. `xpass` means
the claimed bug does not reproduce (the claim is wrong). Every confirmed bug
gets an entry in `BUGS.md` and, after approval, a regression test.
