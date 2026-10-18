# TEST_REPORT.md — tg-digest-userbot (v1 QA, post-fix)

Scope: the whole repository. Method: read-only review → static analysis →
layered tests (unit → fake-client integration → property/fuzz → CLI → perf).
**No real Telegram account was used.** Phase 8 (live smoke) is left to the owner.

## 1. Headline results (after fixes)

| Metric | Before fixes | After fixes |
|---|---|---|
| Tests | 253 passed, 15 xfailed | **288 passed, 1 skipped, 0 failed, 0 xfailed** |
| Bugs open | 12 | **0** (12 found, 12 fixed) |
| Line coverage | 95% | **95%** |
| 100%-coverage modules | 4 | **4** (`timeutil`, `formatter`, `splitter`, `logging_setup`) |
| mypy / bandit / ruff(app) | 4 / 3 / 10 | **0 / 0 / 0** |
| Account-safety scan | Clean | **Clean (re-verified)** |

The full suite **including** the 100 s perf file: `288 passed, 1 skipped` in
171 s. Without the perf file: `285 passed, 1 skipped` in 71 s.

## 2. How to run

```bash
pip install -r requirements.txt
pip install pytest-cov hypothesis jsonschema ruff mypy bandit pip-audit
pytest --cov=config --cov=timeutil --cov=state --cov=logging_setup \
       --cov=formatter --cov=splitter --cov=client --cov=channels \
       --cov=scraper --cov=main
```
Perf file alone: `pytest tests/test_phase7_performance.py -s`.

## 3. Coverage per module (post-fix)

| Module | Stmts | Miss | Cover |
|---|---|---|---|
| `timeutil.py` | 72 | 0 | **100%** |
| `formatter.py` | 86 | 0 | **100%** |
| `splitter.py` | 59 | 0 | **100%** |
| `logging_setup.py` | 20 | 0 | **100%** |
| `config.py` | 43 | 1 | 98% |
| `state.py` | 43 | 2 | 95% |
| `channels.py` | 163 | 8 | 95% |
| `main.py` | 269 | 19 | 93% |
| `scraper.py` | 293 | 22 | 92% |
| `client.py` | 10 | 1 | 90% |
| **TOTAL** | **1058** | **53** | **95%** |

## 4. Static analysis (post-fix)

- **ruff:** app-code `F401` (unused imports), `BLE001` (blind except), `B023`
  (loop-closure), `A`, `E9` — **all clean**. Unused imports were removed; the
  intentional broad excepts are marked `# noqa: BLE001` with a justification.
  Remaining ruff output is style-only (`UP017`/`UP045`/`I001`/`RUF005`) and was
  deliberately left alone (no mass reformatting).
- **mypy:** **0** errors (was 4). Fixed: `ist_str` optional-date guard in
  `scraper.normalize_message`; the loop lambda replaced by `functools.partial`;
  `len(messages or [])` narrows the optional message list.
- **bandit:** **0** issues (was 3 Low). The two `B311` (non-crypto `random` for
  jitter) are marked `# nosec B311` — this is human-like pacing, not a security
  decision. The `B110` (try/except/pass) was replaced by a debug log.
- **pip-audit:** no known vulnerabilities. **compileall:** OK.
- **AST safety scan:** no forbidden call; `ImportChatInviteRequest` only in
  `channels.py` and only under `auto_join`; `JoinChannelRequest` unused.

## 5. Performance (50 channels × 5000 = 250 000 messages)

| Metric | Before | After |
|---|---|---|
| Runtime | 96.8 s | 103.0 s (~6% slower) |
| Peak memory (`tracemalloc`) | 115.6 MB | 115.7 MB |
| Output size | 26.29 MB | 26.30 MB |
| Part files | 100 | 100 |
| `iter_messages` / `get_entity` calls | 50 / 10 | 50 / 10 |

Nothing got materially worse: memory and output are identical; the ~6 s runtime
increase is the added per-message `window.contains()` check plus truncation
bookkeeping and normal variance. No optimisation was attempted.

## 6. Real-Telethon-object verification

`tests/test_real_telethon_objects.py` (12 tests) builds every Telethon type the
fake imitates for real and runs it through the real extraction code, so a
fake/reality disagreement cannot hide again. It pins the BUG-12 root cause
(`Message.poll` → `MessageMediaPoll`), confirms `Message.sender` is a synchronous
property (no per-message network call), and confirms the fake matches
`iter_messages(offset_date=…)` exclusivity. Full findings in `BUGS.md`
("Fake/reality mismatch audit").

## 7. What was NOT tested, and why

- **Phase 8 live smoke test — not run.** Needs the owner's real account; the
  sandbox has no Telegram session. Commands and a verification checklist are
  provided in the hand-off.
- **Real network behaviour** (actual `FloodWaitError`s, real entity resolution,
  real media/album grouping, the login/OTP/2FA flow, `client.py:26`) — simulated
  or uncovered; requires a live session.
- **Real Unicode off the wire** — hyperlink offsets are tested with hand-built
  `MessageEntityTextUrl` on Hindi/emoji/ZWJ/RTL text, not live Telegram data.
- Remaining uncovered lines are defensive branches (invite-import errors, a few
  `fwd`/author variants, the CLI `main()` body, `client.py` login).

## 8. Verdict

**All 12 bugs are fixed and verified; the suite is green (288 passed, 0 failed,
0 xfailed) at 95% coverage with clean static analysis.** The remaining gate is
the owner's live smoke test (Phase 8). Until that passes on the real account,
the honest status is **"ready pending the live smoke test"**, not "ready for
daily use". Recommended (non-blocking) follow-ups: stream the output instead of
buffering all messages, pin the dev/test toolchain in a requirements file, and
clear the remaining style-only ruff findings.
