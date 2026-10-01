#!/usr/bin/env bash
# Copy Porter to the instance, build the image there, and start it with Postgres, Prometheus and Grafana.
#
#   ./deploy/push.sh        run it again after any change: it copies, rebuilds and restarts what changed
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/.state
KEY="$HOME/.ssh/porter-demo.pem"
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new ubuntu@$PUBLIC_IP"

echo "waiting for the instance to finish installing Docker…"
until $SSH test -f /var/lib/cloud/porter-ready 2>/dev/null; do sleep 5; done

# What compose.yaml needs, and nothing else. .env goes too: the key travels over ssh into the
# instance's copy of this folder, and compose hands it to the container. It is never in the image.
rsync -az -e "ssh -i $KEY" --exclude __pycache__ \
  porter Dockerfile .dockerignore requirements.txt compose.yaml prometheus.yml grafana online_retail.db .env \
  "ubuntu@$PUBLIC_IP:porter/"

# The same command as on the laptop, run on the instance: build the image there, start all four.
$SSH "cd porter && docker compose up -d --build && docker compose ps"
echo "Porter: http://$PUBLIC_IP:8080"
