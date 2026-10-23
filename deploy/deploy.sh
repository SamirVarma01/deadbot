#!/usr/bin/env bash
# Copy the project to the VM and (re)start it. Run from the project root on your Mac:
#   ./deploy/deploy.sh ubuntu@<VM_PUBLIC_IP> [path/to/ssh_key]
set -euo pipefail
HOST="${1:?usage: deploy/deploy.sh ubuntu@<ip> [ssh_key]}"
KEY="${2:-}"
SSH=(ssh); [[ -n "$KEY" ]] && SSH+=(-i "$KEY")

grep -q '^POSTGRES_PASSWORD=..*' .env || { echo "Set POSTGRES_PASSWORD in .env first"; exit 1; }
grep -q '^DISCORD_TOKEN=..*' .env || { echo "Set DISCORD_TOKEN in .env first"; exit 1; }

rsync -az --delete -e "${SSH[*]}" \
  --exclude .venv --exclude .git --exclude node_modules --exclude '__pycache__' \
  --exclude .pytest_cache --exclude .ruff_cache --exclude '*.egg-info' \
  ./ "$HOST:deadbot/"

"${SSH[@]}" "$HOST" 'cd deadbot && chmod 600 .env && \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build && \
  docker compose ps'
echo "Deployed. Follow logs with: ${SSH[*]} $HOST 'cd deadbot && docker compose logs -f bot'"
