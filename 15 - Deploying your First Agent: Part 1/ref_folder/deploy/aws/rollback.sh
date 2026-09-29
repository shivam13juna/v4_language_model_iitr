#!/usr/bin/env bash
# Put a previous version back. No build: every version pushed is still an image on the box.
#
#   ./deploy/aws/rollback.sh 2.0.0
set -euo pipefail
cd "$(dirname "$0")/../.."
TAG="${1:?usage: rollback.sh <version that was deployed before>}"
source deploy/aws/.state
ssh -i "$HOME/.ssh/porter-demo.pem" "ubuntu@$PUBLIC_IP" \
  "cd porter && docker image inspect porter:$TAG >/dev/null && PORTER_TAG=$TAG docker compose -f compose.prod.yaml up -d --no-build porter"
curl -s "https://$SITE_ADDRESS/version"; echo
