"""Help text and per-command guides.

These strings are sent with ``parse_mode="html"``. The bot client's default parse
mode stays ``None`` so that dynamic text (errors, digests, channel names) is never
interpreted as markup -- only these static help strings opt in.

Telegram understands a small HTML subset: ``<b> <i> <u> <s> <code> <pre> <a>``.
Anything else (including a literal ``<`` or ``&``) must be escaped.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# The /start and /help message: grouped, one short line per command.
# --------------------------------------------------------------------------
HELP_TEXT = (
    "<b>🤖 tg-digest — control panel</b>\n"
    "<i>Tap a command, or send /help &lt;command&gt;, for its full guide.</i>\n"
    "\n"
    "<b>Account</b>\n"
    "/login — QR login (scan from another device)\n"
    "/login_phone — phone + code login\n"
    "/status — login status\n"
    "/logout — log out\n"
    "/cancel — cancel a login\n"
    "\n"
    "<b>Channels</b>\n"
    "/channels [tag|paused|invalid] — list your channels\n"
    "/add — add a channel (link, @name or id)\n"
    "/remove · /pause · /resume — edit the list\n"
    "/tag · /untag · /groups — organise by tag\n"
    "\n"
    "<b>Runs</b>\n"
    "/run — scrape a time window\n"
    "/history — one channel's entire history\n"
    "/preview — dry run (counts only, no file)\n"
    "/stop — stop the running scrape\n"
    "/last — resend the last digest\n"
    "\n"
    "<b>Schedules</b> (IST, max 5)\n"
    "/schedule add | list | remove | pause | resume\n"
    "\n"
    "<b>Settings &amp; prompts</b>\n"
    "/settings · /set — defaults\n"
    "/prompts · /prompt — summary prompts\n"
    "\n"
    "<b>Files &amp; health</b>\n"
    "/files · /clean · /find — outputs\n"
    "/health · /panic — status &amp; kill switch\n"
    "\n"
    "<i>2FA password के लिए terminal login सबसे सुरक्षित है।</i>"
)

# Commands that do nothing useful without arguments: calling one bare shows its
# guide instead of a terse usage line.
GUIDE_COMMANDS = frozenset({
    "run", "history", "preview", "add", "remove", "pause", "resume", "tag",
    "untag", "set", "prompt", "find", "schedule",
})

_TIP_LIMITS = "<i>One run at a time · 5 min between runs · 12/day · 30 min timeout.</i>"

# --------------------------------------------------------------------------
# Per-command guides.
# --------------------------------------------------------------------------
COMMAND_GUIDES: dict[str, str] = {
    "start": HELP_TEXT,
    "help": (
        "<b>/help — this guide</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/help</code> — the command list\n"
        "<code>/help &lt;command&gt;</code> — the full guide for one command\n\n"
        "<b>Examples</b>\n"
        "<code>/help run</code>\n"
        "<code>/help history</code>\n\n"
        "<i>Tip: tapping a command in the menu sends it bare, which shows its guide.</i>"
    ),
    "login": (
        "<b>/login — QR login</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/login</code>\n\n"
        "Shows a QR image. Scan it from a <b>second</b> device:\n"
        "Telegram → Settings → Devices → Link Desktop Device.\n\n"
        "<b>Notes</b>\n"
        "• Refreshes the QR up to 3 times (~2–3 minutes), then stops.\n"
        "• The <code>tg://login?token=…</code> link is also sent as text.\n"
        "• <code>/cancel</code> aborts it at any point.\n\n"
        "<i>Do this once — the session is saved.</i>"
    ),
    "login_phone": (
        "<b>/login_phone — phone + code login</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/login_phone</code>\n\n"
        "Type your phone number, then the code Telegram sends. Write the code with\n"
        "separators, e.g. <code>1-2-3-4-5</code>.\n\n"
        "<b>Notes</b>\n"
        "• Telegram often rejects codes sent through a chat — prefer <code>/login</code>.\n"
        "• At most 3 code attempts and 3 code requests per hour.\n"
        "• Your code message is deleted immediately after it is read.\n\n"
        "<i>For the 2FA password, the terminal login is safest.</i>"
    ),
    "status": (
        "<b>/status — login status</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/status</code>\n\n"
        "Shows whether you are logged in, your first name, and only the <b>last 2 digits</b>\n"
        "of your phone number."
    ),
    "logout": (
        "<b>/logout — log out</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/logout</code>\n\n"
        "Asks for confirmation, then logs out on Telegram's side and deletes the local\n"
        "session file. You will need to <code>/login</code> again."
    ),
    "cancel": (
        "<b>/cancel — cancel a login</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/cancel</code>\n\n"
        "Aborts a login in progress and clears the saved state."
    ),
    "channels": (
        "<b>/channels — list your channels</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/channels [tag]</code>\n\n"
        "Lists 20 per page with their number, label, tags and a mark for paused or\n"
        "invalid lines. The <b>number</b> is what other commands take.\n\n"
        "<b>Examples</b>\n"
        "<code>/channels</code>\n"
        "<code>/channels jobs</code> — only channels tagged <i>jobs</i>\n"
        "<code>/channels paused</code> — only paused ones\n"
        "<code>/channels invalid</code> — only lines that will not resolve\n\n"
        "<i>Unnamed channels show their resolved title once it is known.</i>"
    ),
    "add": (
        "<b>/add — add a channel</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/add &lt;link|@name|id&gt; [| label]</code>\n\n"
        "Validates the channel with read-only calls and appends it to channels.txt.\n"
        "It shows the resolved title and whether you are a member.\n\n"
        "<b>Examples</b>\n"
        "<code>/add @news</code>\n"
        "<code>/add @news | Daily News</code>\n"
        "<code>/add https://t.me/somechannel | Some</code>\n\n"
        "<b>Notes</b>\n"
        "• If you are not a member, the scraper skips it (it never joins from the bot).\n"
        "• A numeric id that cannot be verified asks for an explicit “add anyway”."
    ),
    "remove": (
        "<b>/remove — remove a channel</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/remove &lt;#|link|name&gt;</code>\n\n"
        "Asks for confirmation, then removes the line. An ambiguous match asks which\n"
        "one you meant.\n\n"
        "<b>Examples</b>\n"
        "<code>/remove 3</code>\n"
        "<code>/remove @news</code>"
    ),
    "pause": (
        "<b>/pause — pause a channel</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/pause &lt;#&gt;</code>\n\n"
        "Marks the line <code>off</code>. The scraper skips it and reports it as\n"
        "<i>paused</i>. The line stays in the file.\n\n"
        "<b>Example</b>\n"
        "<code>/pause 2</code>"
    ),
    "resume": (
        "<b>/resume — resume a channel</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/resume &lt;#&gt;</code>\n\n"
        "Removes the <code>off</code> mark so the channel is scraped again.\n\n"
        "<b>Example</b>\n"
        "<code>/resume 2</code>"
    ),
    "tag": (
        "<b>/tag — add a tag</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/tag &lt;#&gt; &lt;tag&gt;</code>\n\n"
        "Tags let you run a subset with <code>group:&lt;tag&gt;</code>.\n\n"
        "<b>Example</b>\n"
        "<code>/tag 1 news</code>"
    ),
    "untag": (
        "<b>/untag — remove a tag</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/untag &lt;#&gt; &lt;tag&gt;</code>\n\n"
        "<b>Example</b>\n"
        "<code>/untag 1 news</code>"
    ),
    "groups": (
        "<b>/groups — tag groups</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/groups</code>\n\n"
        "Lists your tags with how many channels each one has. Use one with\n"
        "<code>/run today group:&lt;tag&gt;</code>."
    ),
    "run": (
        "<b>/run — scrape a time window</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/run &lt;window&gt; [group:&lt;tag&gt;] [channel:&lt;n&gt;]\n"
        "[dedupe] [min:&lt;N&gt;] [views] [send:no] [resume]</code>\n\n"
        "<b>Window</b> (pick one)\n"
        "• <code>today</code> — from midnight IST\n"
        "• <code>yesterday</code> — the whole previous day\n"
        "• <code>6h</code> · <code>24h</code> · <code>3d</code> — the last N\n"
        "• <code>since-last</code> — since the last successful run\n"
        "• <code>all</code> — the entire history (long!)\n"
        "• <code>YYYY-MM-DD HH:MM YYYY-MM-DD HH:MM</code> — an IST range\n\n"
        "<b>Options</b> (any order, all optional)\n"
        "• <code>group:&lt;tag&gt;</code> — only channels with that tag\n"
        "• <code>channel:&lt;n&gt;</code> — only channel number n\n"
        "• <code>dedupe</code> — drop near-duplicates\n"
        "• <code>min:&lt;N&gt;</code> — skip messages shorter than N\n"
        "• <code>views</code> — include view counts\n"
        "• <code>send:no</code> — don't send me the file\n"
        "• <code>resume</code> — continue a run that stopped\n\n"
        "<b>Examples</b>\n"
        "<code>/run today</code>\n"
        "<code>/run 24h group:jobs min:40</code>\n"
        "<code>/run channel:5 3d</code> — only channel 5, last 3 days\n"
        "<code>/run 2026-10-17 06:00 2026-10-18 18:00</code>\n\n"
        "<i>क्रम मायने नहीं रखता — window पहले या बाद में, दोनों चलेंगे।</i>\n\n" + _TIP_LIMITS
    ),
    "history": (
        "<b>/history — one channel's entire history</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/history &lt;#|@name|link&gt; [dedupe] [min:&lt;N&gt;] [views] [send:yes]</code>\n\n"
        "Reads the channel from its <b>first message</b> until now. Equivalent to\n"
        "<code>/run all channel:&lt;n&gt;</code>.\n\n"
        "<b>Examples</b>\n"
        "<code>/history 3</code>\n"
        "<code>/history @news dedupe min:40</code>\n\n"
        "<b>Notes</b>\n"
        "• The message cap is lifted and the timeout becomes <b>2 hours</b>.\n"
        "• The file is <b>not</b> sent unless you add <code>send:yes</code>.\n"
        "• If it is interrupted, re-send it with <code>resume</code> to continue.\n\n"
        "<i>Long job — keep the bot awake (termux-wake-lock on Android).</i>"
    ),
    "preview": (
        "<b>/preview — dry run</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/preview &lt;window&gt; [group:&lt;tag&gt;]</code>\n\n"
        "Shows how many messages each channel has for that window. <b>No file is\n"
        "written</b> and nothing is sent.\n\n"
        "<b>Example</b>\n"
        "<code>/preview 24h</code>"
    ),
    "stop": (
        "<b>/stop — stop the running scrape</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/stop</code>\n\n"
        "Kills the whole scrape process tree. What was read so far stays in the\n"
        "checkpoint, so you can continue later with <code>resume</code>."
    ),
    "last": (
        "<b>/last — resend the last digest</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/last</code>\n\n"
        "Resends the most recent digest file(s) as documents. Handy when a send\n"
        "failed or you want it again."
    ),
    "schedule": (
        "<b>/schedule — daily runs (IST, max 5)</b>\n\n"
        "<i>/schedule list हर schedule की अगली और पिछली बार भी दिखाता है।</i>\n\n"
        "<b>Usage</b>\n"
        "<code>/schedule add &lt;HH:MM&gt; &lt;window&gt; [group:&lt;tag&gt;]</code>\n"
        "<code>/schedule list</code>\n"
        "<code>/schedule remove &lt;n&gt;</code>\n"
        "<code>/schedule pause &lt;n&gt;</code> · <code>/schedule resume &lt;n&gt;</code>\n\n"
        "<b>Examples</b>\n"
        "<code>/schedule add 09:00 today</code>\n"
        "<code>/schedule add 20:30 24h group:news</code>\n\n"
        "<b>Notes</b>\n"
        "• Times are IST. A missed run is <b>not</b> caught up — the bot offers a\n"
        "  “Run now” button instead.\n"
        "• Scheduled and manual runs share the same limits.\n"
        "• window और group:tag दोनों जाँचे जाते हैं — गलत होने पर जोड़ा नहीं जाएगा।\n\n"
        "<i>The bot must be running for schedules to fire.</i>"
    ),
    "settings": (
        "<b>/settings — current defaults</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/settings</code>\n\n"
        "Shows every setting and its value. Change one with <code>/set</code>."
    ),
    "set": (
        "<b>/set — change a setting</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/set &lt;key&gt; &lt;value&gt;</code>\n\n"
        "<b>Keys</b>\n"
        "• <code>format</code> — json · jsonl · txt\n"
        "• <code>dedupe</code> — on · off\n"
        "• <code>min_chars</code> — 0–500\n"
        "• <code>with_views</code> — on · off\n"
        "• <code>max_chars</code> — 50000–1000000\n"
        "• <code>max_per_channel</code> — 100–20000\n"
        "• <code>retention_days</code> — 1–60\n"
        "• <code>autodelete_minutes</code> — 0–1440 (0 = off)\n"
        "• <code>summary_prompt</code> — a name from /prompts\n\n"
        "<b>Examples</b>\n"
        "<code>/set dedupe on</code>\n"
        "<code>/set summary_prompt exam_alerts</code>"
    ),
    "prompts": (
        "<b>/prompts — summary prompts</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/prompts</code>\n\n"
        "Lists the available prompts and marks the one currently embedded in new\n"
        "digests. The bot <b>never</b> calls an AI service — it only stores and\n"
        "embeds this text."
    ),
    "prompt": (
        "<b>/prompt — show one prompt</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/prompt &lt;name&gt;</code>\n\n"
        "Sends the prompt's text back to you (as a message, or a <code>.md</code> file if it\n"
        "is long).\n\n"
        "<b>Example</b>\n"
        "<code>/prompt detailed</code>"
    ),
    "files": (
        "<b>/files — recent outputs</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/files</code>\n\n"
        "Lists recent digests with a button to resend any of them, plus the total\n"
        "count and size. /clean से पुरानी files हटाएँ।"
    ),
    "clean": (
        "<b>/clean — delete stored files</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/clean</code> — older than <code>retention_days</code>\n"
        "<code>/clean 3d</code> (or <code>/clean 3</code>) — older than 3 days\n"
        "<code>/clean all</code> — <b>every</b> digest file, right now\n\n"
        "पहले दिखाता है कि कौन-सी files जाएँगी (नाम, size, कुल) — फिर confirmation माँगता है।\n"
        "हटाने के बाद बताता है कितनी हटीं और कितनी जगह खाली हुई।\n\n"
        "<b>Example</b>\n"
        "<code>/clean all</code>"
    ),
    "find": (
        "<b>/find — search saved digests</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/find &lt;keyword&gt; [6h|24h|3d]</code>\n\n"
        "Searches the saved digest files <b>locally</b> — no Telegram call. Shows up to\n"
        "10 matches with channel, time, a snippet and a link.\n\n"
        "<b>Example</b>\n"
        "<code>/find admit-card 24h</code>"
    ),
    "health": (
        "<b>/health — status check</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/health</code>\n\n"
        "Logged in or not · last run · skipped channels · next scheduled run · free\n"
        "disk space · library versions · uptime. No secrets, no full phone number."
    ),
    "panic": (
        "<b>/panic — kill switch</b>\n\n"
        "<b>Usage</b>\n"
        "<code>/panic</code>\n\n"
        "Two confirmations, then it: pauses all schedules, stops any running scrape,\n"
        "logs out and deletes the session, and deletes outputs and <code>.cache/</code>.\n\n"
        "<b>Keeps</b> <code>channels.txt</code>, <code>settings.json</code>, and the run\n"
        "history/lock so your daily limits survive.\n\n"
        "<i>Use it if you think your account is compromised.</i>"
    ),
}


def esc(text: object) -> str:
    """Escape text for Telegram's HTML parse mode.

    Any dynamic value interpolated into a help/guide message must go through
    this, or a stray '<' or '&' makes Telegram reject the whole message.
    """
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def guide_for(command: str) -> "str | None":
    """Return the guide for a command name (with or without the leading slash)."""
    name = (command or "").strip().lstrip("/").lower()
    return COMMAND_GUIDES.get(name)
