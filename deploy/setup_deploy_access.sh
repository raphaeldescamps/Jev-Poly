#!/usr/bin/env bash
# One-time: let GitHub Actions log in as 'bot' and restart only the bot service.
# Run once on the server:  sudo bash /home/bot/jev-poly/deploy/setup_deploy_access.sh
set -euo pipefail
sudo -u bot -H bash -euc '
  mkdir -p ~/.ssh && chmod 700 ~/.ssh
  [ -f ~/.ssh/deploy_key ] || ssh-keygen -q -t ed25519 -N "" -C github-deploy -f ~/.ssh/deploy_key
  grep -qf ~/.ssh/deploy_key.pub ~/.ssh/authorized_keys 2>/dev/null || cat ~/.ssh/deploy_key.pub >> ~/.ssh/authorized_keys
  chmod 600 ~/.ssh/authorized_keys
'
RULE=/etc/sudoers.d/polybot
cat <<'RULES' | sudo tee "$RULE.tmp" >/dev/null
bot ALL=(root) NOPASSWD: /usr/bin/systemctl enable --now polybot, /usr/bin/systemctl restart polybot, /usr/bin/systemctl is-active polybot, /usr/bin/journalctl -u polybot -n 15 --no-pager -o cat, /usr/bin/journalctl -u polybot -n 30 --no-pager -o cat
RULES
sudo visudo -cf "$RULE.tmp" >/dev/null && sudo mv "$RULE.tmp" "$RULE" && sudo chmod 440 "$RULE"

echo
echo "=== Copy everything between the lines into the GitHub secret DEPLOY_SSH_KEY ==="
sudo cat /home/bot/.ssh/deploy_key
echo "=== end ==="
echo
echo "Server public IP for the GitHub secret DEPLOY_HOST:"
curl -s https://checkip.amazonaws.com
echo
echo "After saving both secrets: sudo rm /home/bot/.ssh/deploy_key"
