"""Entry point: parse arguments, resolve channels, scrape, and write the digest.

Read-only end to end. The only side effects are local files:
  * output/digest_*.json            the digest
  * .cache/<run_id>.jsonl           per-channel checkpoint for --resume
  * state.json                      last successful run (for --since-last-run)
  * logs/run.log                    full log
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import sys
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from tqdm import tqdm

import config
import state as state_mod
from channels import (
    JoinState,
    parse_channels_file,
    resolve_spec,
)
from client import build_client, login
from config import (
    CACHE_DIR,
    CHANNEL_WAIT_MAX_SECONDS,
    CHANNEL_WAIT_MIN_SECONDS,
    CHANNELS_PATH,
    DEFAULT_AI_INSTRUCTIONS,
    DEFAULT_MAX_CHARS,
    DEFAULT_MAX_FLOOD_WAIT,
    DEFAULT_MAX_JOINS,
    DEFAULT_MAX_PER_CHANNEL,
    DEFAULT_MIN_CHARS,
    DEFAULT_TIMEZONE,
    LOG_DIR,
    OUTPUT_DIR,
    SESSION_BASENAME,
    STATE_PATH,
    load_credentials,
)
from formatter import (
    build_digest,
    dumps_compact,
    dumps_pretty,
    estimate_tokens,
    ist_str,
    to_jsonl,
    to_txt,
)
from logging_setup import setup_logging
from scraper import ScrapeOptions, count_channel, scrape_channel
from splitter import split_digest
from timeutil import get_tz, resolve_window


# --------------------------------------------------------------------------
# Checkpointing
# --------------------------------------------------------------------------
class Checkpoint:
    """Append-only record of finished channels, so --resume can skip them."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def load_done(self) -> dict[str, dict[str, Any]]:
        done: dict[str, dict[str, Any]] = {}
        if not self.path.exists():
            return done
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("type") == "channel" and record.get("key"):
                done[record["key"]] = record["channel"]
        return done

    def _append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append_channel(self, key: str, channel: dict[str, Any]) -> None:
        self._append({"type": "channel", "key": key, "channel": channel})

    def append_skip(self, key: str, reason: str) -> None:
        self._append({"type": "skip", "key": key, "reason": reason})


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def _safe_name(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    return (slug[:40] or "channel")


def render_part(part: dict[str, Any], fmt: str, pretty: bool) -> str:
    if fmt == "json":
        return dumps_pretty(part) if pretty else dumps_compact(part)
    if fmt == "jsonl":
        return to_jsonl(part)
    return to_txt(part)


def _name_exists(out_dir: Path, base: str) -> bool:
    """True if any output file already starts with this base name."""
    return any(out_dir.glob(f"{base}.*")) or any(out_dir.glob(f"{base}_*"))


def _unique_base(out_dir: Path, tz) -> str:
    """A digest base name that will not overwrite an existing file.

    Minute resolution first; then seconds; then a numeric suffix.
    """
    base = f"digest_{datetime.now(tz).strftime('%Y-%m-%d_%H%M')}"
    if not _name_exists(out_dir, base):
        return base
    base = f"digest_{datetime.now(tz).strftime('%Y-%m-%d_%H%M%S')}"
    if not _name_exists(out_dir, base):
        return base
    counter = 2
    while _name_exists(out_dir, f"{base}_{counter}"):
        counter += 1
    return f"{base}_{counter}"


def write_outputs(
    args: argparse.Namespace,
    ai_instructions: str,
    meta: dict[str, Any],
    channels: list[dict[str, Any]],
    tz,
) -> list[tuple[Path, str]]:
    """Write the digest (single file, parts, or one file per channel).

    An existing file is never overwritten: the name falls back to seconds, then
    to a numeric suffix.
    """
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = _unique_base(out_dir, tz)
    ext = args.format
    written: list[tuple[Path, str]] = []

    def emit(name: str, part: dict[str, Any]) -> None:
        text = render_part(part, args.format, args.pretty)
        path = out_dir / name
        path.write_text(text, encoding="utf-8")
        written.append((path, text))

    if args.per_channel_files:
        total = max(len(channels), 1)
        for index, channel in enumerate(channels, start=1):
            part = build_digest(ai_instructions, {**meta, "part": f"{index}/{total}"}, [channel])
            emit(f"{base}_{_safe_name(channel['name'])}.{ext}", part)
    else:
        parts = split_digest(ai_instructions, meta, channels, args.max_chars)
        for index, part in enumerate(parts, start=1):
            name = f"{base}.{ext}" if len(parts) == 1 else f"{base}_part{index}.{ext}"
            emit(name, part)
    return written


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tg-digest-userbot",
        description="Read-only Telegram digest of chosen channels, written as AI-ready JSON.",
    )

    window = parser.add_argument_group("time window (choose one)")
    window.add_argument("--today", action="store_true", help="from 00:00 IST today until now")
    window.add_argument("--yesterday", action="store_true", help="all of yesterday (IST)")
    window.add_argument("--last", metavar="DUR", help="e.g. 6h, 24h, 3d, 1w")
    window.add_argument("--since", metavar="'YYYY-MM-DD HH:MM'", help="range start (IST)")
    window.add_argument("--until", metavar="'YYYY-MM-DD HH:MM'", help="range end (IST)")
    window.add_argument("--since-last-run", action="store_true", help="since the last successful run")

    parser.add_argument("--max-per-channel", type=int, default=DEFAULT_MAX_PER_CHANNEL,
                        help="safety cap of messages read per channel")
    parser.add_argument("--max-joins", type=int, default=DEFAULT_MAX_JOINS,
                        help="max chats to join when --auto-join is set")
    parser.add_argument("--max-flood-wait", type=int, default=DEFAULT_MAX_FLOOD_WAIT,
                        help="skip a channel if FloodWait exceeds this many seconds")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                        help="split output into parts above this many characters")
    parser.add_argument("--dedupe", action="store_true", help="drop identical texts within a channel")
    parser.add_argument("--min-chars", type=int, default=DEFAULT_MIN_CHARS,
                        help="drop text-only messages shorter than N characters")
    parser.add_argument("--with-views", action="store_true", help="include message view counts")
    parser.add_argument("--auto-join", action="store_true",
                        help="allow joining chats that are not yet joined (rate-limited)")
    parser.add_argument("--format", choices=["json", "jsonl", "txt"], default="json")
    parser.add_argument("--pretty", action="store_true", help="indent=2 (for reading, not for AI)")
    parser.add_argument("--per-channel-files", action="store_true", help="one output file per channel")
    parser.add_argument("--summary-prompt-file", help="override the default ai_instructions text")
    parser.add_argument("--resume", action="store_true", help="continue a run that stopped midway")
    parser.add_argument("--dry-run", action="store_true",
                        help="report channels and counts without writing any file")
    parser.add_argument("--channels-file", default=str(CHANNELS_PATH))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument("--verbose", action="store_true")
    return parser


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
async def run(args: argparse.Namespace) -> int:
    config.ensure_directories()
    log = setup_logging(LOG_DIR / "run.log", verbose=args.verbose)
    tz = get_tz(args.timezone)

    ai_instructions = DEFAULT_AI_INSTRUCTIONS
    if args.summary_prompt_file:
        try:
            prompt_text = Path(args.summary_prompt_file).read_text(encoding="utf-8").strip()
        except OSError as exc:
            print(f"Error: cannot read --summary-prompt-file {args.summary_prompt_file}: {exc.strerror or exc}")
            return 2
        if prompt_text:
            ai_instructions = prompt_text
        else:
            print("Warning: --summary-prompt-file is empty; using the default ai_instructions.")

    # Validate the output directory up front (before any network work).
    out_dir = Path(args.output_dir)
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / ".write_probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        print(f"Error: output directory is not writable: {out_dir} ({exc.strerror or exc})")
        return 2

    last_run = state_mod.get_last_run(STATE_PATH)
    if args.since_last_run:
        now_utc = datetime.now(timezone.utc)
        problem = None
        if last_run is None:
            problem = "no previous successful run was recorded"
        elif last_run > now_utc:
            problem = "the recorded timestamp is in the future"
        if problem:
            print(f"Warning: --since-last-run cannot be used ({problem}); falling back to --last 24h.")
            args.since_last_run = False
            args.last = "24h"
    window = resolve_window(
        today=args.today,
        yesterday=args.yesterday,
        last=args.last,
        since=args.since,
        until=args.until,
        since_last_run=args.since_last_run,
        last_run=last_run,
        tz=tz,
    )
    log.info("Window UTC: %s .. %s", window.since.isoformat(), window.until.isoformat())

    channels_path = Path(args.channels_file)
    if not channels_path.exists():
        print(
            f"Channels file not found: {channels_path}\n"
            "Copy channels.txt.example to channels.txt and add your channels."
        )
        return 1

    specs = parse_channels_file(channels_path)
    if not specs:
        print(f"No channels found in {channels_path}.")
        return 1

    credentials = load_credentials()
    client = build_client(credentials, SESSION_BASENAME)
    await login(client, credentials.phone)

    try:
        join_state = JoinState(auto_join=args.auto_join, max_joins=args.max_joins)
        skipped: list[dict[str, str]] = []

        print(f"Resolving {len(specs)} channel(s)...")
        resolved_list = []
        for spec in specs:
            resolved = await resolve_spec(client, spec, join_state)
            resolved_list.append(resolved)
            if not resolved.ok:
                skipped.append({"channel": spec.label or spec.value, "reason": resolved.reason})
                log.info("skip %s: %s", spec.raw, resolved.reason)

        # De-duplicate by the resolved numeric chat id (the same channel listed
        # twice, e.g. as a link and as @username). Keep the first occurrence.
        seen_ids: set[int] = set()
        unique_resolved = []
        for resolved in resolved_list:
            entity_id = getattr(resolved.entity, "id", None) if resolved.ok else None
            if entity_id is not None and entity_id in seen_ids:
                log.info("duplicate channel %s (id %s) already listed; skipping", resolved.spec.raw, entity_id)
                continue
            if entity_id is not None:
                seen_ids.add(entity_id)
            unique_resolved.append(resolved)
        resolved_list = unique_resolved

        opts = ScrapeOptions(
            tz=tz,
            max_per_channel=args.max_per_channel,
            with_views=args.with_views,
            dedupe=args.dedupe,
            min_chars=args.min_chars,
            max_flood_wait=args.max_flood_wait,
        )

        if args.dry_run:
            print("\nDRY RUN - nothing will be written.\n")
            for resolved in resolved_list:
                if resolved.ok:
                    count = await count_channel(client, resolved, window, opts)
                    print(f"  OK    {resolved.name}  (~{count} message(s) in window)")
                else:
                    print(f"  SKIP  {resolved.spec.raw}  ({resolved.reason})")
            return 0

        ok_list = [resolved for resolved in resolved_list if resolved.ok]

        run_id = state_mod.get_run_id(CACHE_DIR) if args.resume else None
        if run_id is None:
            run_id = datetime.now(tz).strftime("%Y%m%d_%H%M%S")
            state_mod.set_run_id(CACHE_DIR, run_id)
        checkpoint = Checkpoint(CACHE_DIR / f"{run_id}.jsonl")
        done = checkpoint.load_done() if args.resume else {}

        results: list[dict[str, Any]] = []
        truncated_channels: list[str] = []
        for resolved in ok_list:
            key = resolved.spec.raw
            if key in done:
                results.append(done[key])
                if done[key].get("truncated"):
                    truncated_channels.append(done[key].get("name", resolved.name))
                log.info("resume: %s already collected", resolved.name)
                continue

            bar = tqdm(desc=f"{resolved.name[:28]:<28}", unit="msg", leave=False)
            truncated = False
            try:
                messages, reason, truncated = await scrape_channel(
                    client, resolved, window, opts, partial(bar.update)
                )
            except Exception as exc:  # noqa: BLE001 - belt-and-braces; one channel must not kill the run
                messages, reason, truncated = None, f"unexpected_error:{type(exc).__name__}", False
            finally:
                bar.close()

            if reason:
                skipped.append({"channel": resolved.name, "reason": reason})
                checkpoint.append_skip(key, reason)
                log.info("skip %s: %s", resolved.name, reason)
            else:
                channel = {
                    "name": resolved.name,
                    "username": resolved.username,
                    "type": resolved.kind,
                    "link_base": resolved.link_base,
                    "count": len(messages or []),
                    "messages": messages or [],
                }
                if truncated:
                    channel["truncated"] = True
                    channel["cap"] = opts.max_per_channel
                    truncated_channels.append(resolved.name)
                results.append(channel)
                checkpoint.append_channel(key, channel)
                log.info("done %s: %d message(s)", resolved.name, len(messages or []))

            await asyncio.sleep(random.uniform(CHANNEL_WAIT_MIN_SECONDS, CHANNEL_WAIT_MAX_SECONDS))  # nosec B311 - channel jitter

        messages_total = sum(channel["count"] for channel in results)
        now_utc = datetime.now(timezone.utc)
        meta: dict[str, Any] = {
            "generated_at": ist_str(now_utc, tz) + " IST",
            "timezone": args.timezone,
            "from": ist_str(window.since, tz),
            "to": ist_str(window.until, tz),
            "channels_ok": len(results),
            "messages_total": messages_total,
            "part": "1/1",
        }
        if skipped:
            meta["skipped"] = skipped
        if truncated_channels:
            meta["truncated_channels"] = truncated_channels

        written = write_outputs(args, ai_instructions, meta, results, tz)
        combined_text = "\n".join(text for _, text in written)
        estimate = estimate_tokens(combined_text)

        # Update state ONLY now that the output is safely on disk.
        if not args.dry_run:
            state_mod.set_last_run(STATE_PATH, window.until, tz)

        print()
        for path, text in written:
            print(f"wrote {path.name}  ({len(text):,} chars)")
        print(
            f"\nchannels_ok={len(results)}  messages_total={messages_total}  "
            f"skipped={len(skipped)}  parts={len(written)}"
        )
        print(f"total chars: {estimate['chars']:,}")
        print(f"estimated tokens (Latin, chars/4):      ~{estimate['est_tokens_latin']:,}")
        print(f"estimated tokens (Devanagari, chars/1.5): ~{estimate['est_tokens_devanagari']:,}")
        print(f"estimated tokens (primary, ratio={estimate['devanagari_ratio']}): "
              f"~{estimate['est_tokens_primary']:,}")
        return 0
    finally:
        await client.disconnect()


def main() -> None:
    args = build_parser().parse_args()
    try:
        exit_code = asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nInterrupted. Re-run with --resume to continue.", file=sys.stderr)
        sys.exit(130)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
