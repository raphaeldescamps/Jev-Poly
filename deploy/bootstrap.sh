#!/usr/bin/env bash
# One-time server setup for Ubuntu 24.04. Run as the default 'ubuntu' user:
#   bash bootstrap.sh <git-clone-url>
set -euo pipefail
REPO_URL="${1:?usage: bash bootstrap.sh <git-clone-url>}"

sudo apt-get update -y
sudo apt-get install -y python3-venv git unattended-upgrades
# 1 GB swap so pip installs do not run out of memory on small plans
if [ ! -f /swapfile ]; then
  sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile
  sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

id bot >/dev/null 2>&1 || sudo adduser --disabled-password --gecos "" bot
# Note: "sudo -i" mangles multi-line commands, so use -u/-H and an explicit cd.
sudo -u bot -H bash -euc "
  cd /home/bot
  if [ -d jev-poly/.git ]; then git -C jev-poly pull --ff-only; else rm -rf jev-poly; git clone '$REPO_URL' jev-poly; fi
  cd jev-poly
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
  if [ ! -f .env ]; then cp .env.example .env; chmod 600 .env; sed -i 's/^DRY_RUN=.*/DRY_RUN=true/' .env; fi
"
sudo cp /home/bot/jev-poly/deploy/polybot.service /etc/systemd/system/
sudo systemctl daemon-reload

echo
echo "Polymarket API reachability from this server:"
curl -s -o /dev/null -w "  clob.polymarket.com -> HTTP %{http_code}\n" https://clob.polymarket.com/time || true
echo
echo "Next steps:"
echo "  sudo -iu bot nano jev-poly/.env                       # add JEV_API_KEY etc."
echo "  sudo -iu bot bash -c 'cd jev-poly && .venv/bin/python -m scripts.wallet new'"
echo "  sudo systemctl enable --now polybot && journalctl -u polybot -f"
