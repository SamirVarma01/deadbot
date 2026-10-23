#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu VM (tested on Azure B1s, Ubuntu 24.04).
# Run on the VM:  bash setup-vm.sh
set -euo pipefail

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"
sudo systemctl enable --now docker

# Small swap file as a safety net for image builds.
if ! swapon --show | grep -q /swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# Daily Postgres backup at 04:00 UTC, keeping the last 7 days.
mkdir -p "$HOME/backups"
CRON='0 4 * * * cd $HOME/deadbot && docker compose exec -T postgres pg_dump -U deadbot deadbot | gzip > $HOME/backups/deadbot-$(date +\%F).sql.gz && find $HOME/backups -name "*.sql.gz" -mtime +7 -delete'
# `|| true`: with set -e/pipefail, an empty crontab would otherwise abort before the echo.
{ crontab -l 2>/dev/null | grep -v 'pg_dump -U deadbot' || true; echo "$CRON"; } | crontab -

echo "Done. Log out and back in (so the docker group applies), then deploy from your Mac."
