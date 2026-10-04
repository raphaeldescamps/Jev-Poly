#!/usr/bin/env bash
# Runs on the server as user 'bot', called by the GitHub Actions workflow over SSH.
#   remote.sh deploy     pull code, install deps, (re)start the bot
#   remote.sh check      one test call to Jev (no order)
#   remote.sh status     service state and recent log lines
#   remote.sh evaluate   score Jev's logged calls
#   remote.sh recent     last rows of trades.csv and outcomes.csv
set -euo pipefail
BRANCH="${2:-claude/polymarket-btc-trading-bot-vqmjwr}"
cd /home/bot/jev-poly

mode() { echo "Mode: $(grep -E '^DRY_RUN=' .env | cut -d= -f2 | awk '{print $1}' | sed 's/true/DRY RUN (no orders)/;s/false/LIVE/')"; }

case "${1:-}" in
  deploy)
    git fetch -q origin "$BRANCH"
    git checkout -q "$BRANCH"
    git merge -q --ff-only "origin/$BRANCH"
    echo "Code: $(git log -1 --format='%h %s')"
    .venv/bin/pip install -q -r requirements.txt
    mode
    sudo /usr/bin/systemctl enable --now polybot
    sudo /usr/bin/systemctl restart polybot
    sleep 5
    sudo /usr/bin/systemctl is-active polybot
    sudo /usr/bin/journalctl -u polybot -n 15 --no-pager -o cat
    ;;
  check)
    .venv/bin/python -m scripts.jev_check
    ;;
  status)
    mode
    sudo /usr/bin/systemctl is-active polybot || true
    sudo /usr/bin/journalctl -u polybot -n 30 --no-pager -o cat
    ;;
  evaluate)
    .venv/bin/python -m scripts.evaluate
    ;;
  recent)
    echo "--- last windows (trades.csv) ---"; tail -n 12 trades.csv 2>/dev/null || echo "none yet"
    echo "--- last outcomes (outcomes.csv) ---"; tail -n 12 outcomes.csv 2>/dev/null || echo "none yet"
    ;;
  *)
    echo "usage: remote.sh deploy|check|status|evaluate|recent" >&2; exit 2 ;;
esac
