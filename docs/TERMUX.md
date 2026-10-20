# Running on Termux (Android)

The bot and the scraper both run fine on Termux. The one thing to understand is
that **Android will kill background work when the screen is off** unless you
hold a wake lock — which is why a scheduled run can be missed.

## 1. Install

```bash
pkg update && pkg upgrade
pkg install python git tmux
git clone https://github.com/pavnxet/tg-digest-userbot
cd tg-digest-userbot
pip install -r requirements.txt
```

Everything is pure Python — no compiled or Rust dependencies — so no build
toolchain is needed.

## 2. Give it storage access (only if you want files in shared storage)

```bash
termux-setup-storage      # grants access to ~/storage
```

By default everything stays inside the repo folder (`output/`, `.cache/`,
`logs/`), which needs no special permission.

## 3. Keep it awake

```bash
termux-wake-lock          # acquire the wake lock
```

Without this, Android may suspend Termux when the screen is off, and:

- a **scheduled run** will simply not fire — the bot does not catch up. On the
  next start it tells you what was missed and offers a **Run now** button;
- a **running scrape** may be paused or killed.

Release it later with `termux-wake-lock -r` (or by closing the session).

Also turn off battery optimisation for Termux in **Android Settings → Apps →
Termux → Battery → Unrestricted**.

## 4. Keep it running across sessions

`tmux` survives a disconnect and a closed terminal:

```bash
./deploy/tmux_run.sh          # starts the bot in a detached tmux session
tmux attach -t tg-digest      # watch it
./deploy/tmux_run.sh stop     # stop it
```

Detach from tmux with **Ctrl+B then D**.

## 5. Start it automatically at boot (optional)

Install the **Termux:Boot** app (F-Droid), then create `~/.termux/boot/start-tg-digest`:

```bash
#!/data/data/com.termux/files/usr/bin/sh
termux-wake-lock
cd ~/tg-digest-userbot
tmux new-session -d -s tg-digest 'python bot.py'
```

Make it executable:

```bash
chmod +x ~/.termux/boot/start-tg-digest
```

Termux:Boot only runs scripts on a real device reboot (not when you just reopen
the app).

## 6. Why a schedule can still be missed

Even with a wake lock, Android may kill Termux under memory pressure, or the
phone may be off. Missed runs are **not** caught up automatically — that is
deliberate, so you don't get a burst of stale digests. The bot reports the miss
and lets you run it with one tap.

If you need schedules to be reliable, run the bot on a small always-on VPS
instead (see `deploy/systemd/`).

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError` after a reboot | Re-run `pip install -r requirements.txt` (Termux sometimes resets the environment on upgrade). |
| Bot stops when the screen goes off | `termux-wake-lock`, and set Termux's battery setting to *Unrestricted*. |
| `Permission denied` writing outside the repo | `termux-setup-storage`, or keep outputs inside the repo folder. |
| `database is locked` | A scrape is using the session file. Wait for it to finish. |
