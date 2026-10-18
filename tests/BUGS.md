# BUGS.md — tg-digest-userbot (v1 QA → fixed)

All 12 bugs found in the QA pass are **fixed**, each with a regression test that
is now a normal (passing) test — no `xfail(strict=True)` remains. Ranked by
severity. **No Critical (account-endangering) bugs were ever found.**

| ID | Sev | Module | Status | Regression test |
|---|---|---|---|---|
| BUG-02 | High | scraper.py / main.py | **Fixed** | `test_phase4_integration.py::test_unexpected_error_in_one_channel_does_not_abort_run` |
| BUG-12 | High | scraper.py | **Fixed** | `test_phase3_extraction.py::test_poll_extraction` + `test_real_telethon_objects.py::test_real_poll_*` |
| BUG-01 | Medium | channels.py / main.py | **Fixed** | `test_phase4_integration.py::test_duplicate_channel_forms_produce_one_entry` |
| BUG-07 | Medium | channels.py | **Fixed** | `test_phase3_channels_parse.py::test_schemeless_t_me_url`, `::test_url_with_query_string_and_fragment`, `::test_post_link_resolves_to_channel`, `::test_private_post_link_resolves_to_channel_id` |
| BUG-08 | Medium | channels.py | **Fixed** | `test_phase3_channels_parse.py::test_utf8_bom_is_stripped` |
| BUG-03 | Medium | main.py | **Fixed** | `test_phase6_cli.py::test_two_runs_same_minute_do_not_overwrite` |
| BUG-04 | Medium | main.py | **Fixed** | `test_phase6_cli.py::test_empty_prompt_file_falls_back_to_default` |
| BUG-09 | Medium | scraper.py / main.py | **Fixed** | `test_phase4_integration.py::test_truncation_is_visible_in_meta`, `::test_no_truncation_fields_when_nothing_truncated` |
| BUG-05 | Low | main.py | **Fixed** | `test_phase6_cli.py::test_missing_summary_prompt_file_is_friendly` |
| BUG-06 | Low | main.py | **Fixed** | `test_phase6_cli.py::test_unwritable_output_dir_is_friendly` |
| BUG-10 | Low | timeutil.py / scraper.py | **Fixed** | `test_phase3_time.py::test_window_contains_is_half_open`, `::test_window_contains_is_actually_used_by_scraper` |
| BUG-11 | Low | main.py | **Fixed** | `test_phase4_integration.py::test_since_last_run_future_falls_back_to_24h`, `::test_since_last_run_corrupt_state_falls_back_to_24h` |

---

## What was changed, per bug

**BUG-02 (High) — unexpected error aborts the run.** Added a final
`except Exception` in `scraper.scrape_channel` returning
`f"unexpected_error:{type(exc).__name__}"`, plus a belt-and-braces `except
Exception` around the per-channel call in `main.run`. `KeyboardInterrupt`,
`SystemExit` and `asyncio.CancelledError` are `BaseException`, so they still
propagate and stop the run cleanly (checkpoint kept, `state.json` untouched).

**BUG-12 (High) — polls never captured.** `extract_poll` now reads the real
`Poll` from `message.media.poll` (not the `MessageMediaPoll` wrapper that
`message.poll` returns), and a `_as_text` helper accepts both `str` and
`TextWithEntities` for the question and each answer. Poll-only messages are now
kept.

**BUG-01 (Medium) — duplicate channels.** After resolution, `main.run`
de-duplicates by the resolved numeric chat id, keeping the first occurrence and
its label; the duplicate is logged (not added to `meta.skipped`).

**BUG-07 / BUG-08 (Medium) — parser.** `parse_channel_line` now strips the
scheme, `t.me`/`telegram.me` host, query and fragment, a trailing slash, and a
`/123` post suffix; resolves a private post link `c/<id>/<msg>` to the channel
id `-100<id>`; and turns anything still unparseable into an `invalid` spec that
is reported as `invalid_entry` (never silently dropped). `parse_channels_file`
reads with `utf-8-sig` (BOM) and `splitlines` (CRLF).

**BUG-09 (Medium) — silent truncation.** `_collect`/`scrape_channel` now return
a `truncated` flag; a truncated channel gets `"truncated": true` and `"cap": N`
and its name is listed in `meta.truncated_channels`. The fields are omitted when
nothing was truncated. Schema, README and the default `ai_instructions` updated.

**BUG-03 (Medium) — overwrite.** `write_outputs` uses a `_unique_base` helper:
minute → seconds → numeric suffix; it never overwrites.

**BUG-04 (Medium) — empty prompt.** An empty `--summary-prompt-file` now warns
and falls back to the default `ai_instructions` instead of deleting the key.

**BUG-05 / BUG-06 (Low) — friendly errors.** A missing/unreadable prompt file
and an unwritable output directory are validated **before any network work** and
produce a one-line message with exit code 2 (no traceback). `main` also catches
`OSError` as a safety net.

**BUG-10 (Low) — boundary.** `Window.contains` is now half-open
(`since <= t < until`) and is the single source of truth used by both
`_collect` and `count_channel`. README documents start-inclusive/end-exclusive.

**BUG-11 (Low) — future state.** `--since-last-run` with a missing, corrupt or
future timestamp prints a warning and falls back to `--last 24h`.

---

## Fake/reality mismatch audit (post-fix)

The poll bug happened because a fake and reality disagreed, so every Telethon
type the fake imitates is now built **for real** and pushed through the real
extraction code (`tests/test_real_telethon_objects.py`, 12 tests). Findings:

1. **`Message.poll` returns the `MessageMediaPoll` wrapper** (root cause of
   BUG-12) — now asserted against the installed Telethon and handled.
2. **`Message.sender` is a synchronous property** returning the cached
   `_sender` — it does **not** make a per-message network call. Confirmed by
   `test_real_sender_property_is_synchronous_no_network`. Caveat: in real group
   chats `_sender` is only populated when Telegram includes the sender in the
   response, so `author` may be absent for some group messages (the fake always
   sets it). Documented, not a bug.
3. **`iter_messages(offset_date=…)` is exclusive** (returns messages strictly
   older than the date). The fake matches this; verified by
   `test_fake_iter_messages_matches_offset_date_semantics`.
4. **Forwarded-from-a-user (non-hidden):** `fwd_from.from_name` is `None` and
   only `from_id` is set, so `forward_source` returns `None` and the `fwd` field
   is omitted. This is a real limitation (resolving the name would need an extra
   request); documented, not fixed.
5. `FakeTelegramClient.__call__` dispatches by request class name vs Telethon's
   by-type dispatch — equivalent for the three requests the app uses.

## Safety note (unchanged)

The read-only guarantee holds and is re-verified after the fixes: the AST scan
(`tests/test_phase2_readonly.py`) finds no `send_*`, `edit_*`, `delete_*`,
`forward_*`, `SendReactionRequest`, `ReadHistoryRequest`, `mark_read` or
`send_read_acknowledge` call in any module; `ImportChatInviteRequest` remains
gated behind `--auto-join`; `JoinChannelRequest` is unused. The integration
tests also assert at runtime that no write/send/read-ack method is ever called.
