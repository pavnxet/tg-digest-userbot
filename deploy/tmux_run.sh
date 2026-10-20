#!/usr/bin/env bash
# Keep the control bot alive in a detached tmux session.
#
#   ./deploy/tmux_run.sh          # start (or restart) the bot
#   tmux attach -t tg-digest      # watch it
#   ./deploy/tmux_run.sh stop     # stop it
#
# tmux survives an SSH disconnect, which is the usual reason a bot "dies".
set -euo pipefail

SESSION="tg-digest"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "${1:-start}" in
  start)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "session '$SESSION' already running — attach with: tmux attach -t $SESSION"
      exit 0
    fi
    command -v tmux >/dev/null || { echo "tmux is not installed (apt install tmux / pkg install tmux)"; exit 1; }
    tmux new-session -d -s "$SESSION" -c "$DIR" "python bot.py 2>&1 | tee -a logs/bot_console.log"
    echo "started in tmux session '$SESSION' (cd $DIR)"
    ;;
  stop)
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "stopped '$SESSION'" || echo "no session named '$SESSION'"
    ;;
  status)
    tmux has-session -t "$SESSION" 2>/dev/null && echo "running" || echo "not running"
    ;;
  *)
    echo "usage: $0 [start|stop|status]"; exit 2
    ;;
esac
