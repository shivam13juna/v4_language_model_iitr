#!/usr/bin/env bash
# Ship one version to the instance, build it there, and start it.
#
#   ./deploy/aws/push.sh 2.0.0
#
# The image is built ON the instance, from the same Dockerfile: a machine with no build cache
# and nothing of yours installed is the clean environment the image has to work in.
# Needs deploy/aws/.state (from create.sh) and .env.prod beside this repo's .env — see DEPLOY.md.
set -euo pipefail
cd "$(dirname "$0")/../.."
TAG="${1:?usage: push.sh <version>}"
source deploy/aws/.state
KEY="$HOME/.ssh/porter-demo.pem"
SSH=(ssh -i "$KEY" -o StrictHostKeyChecking=accept-new "ubuntu@$PUBLIC_IP")

echo "waiting for first-boot setup…"
until "${SSH[@]}" test -f /var/lib/cloud/porter-ready 2>/dev/null; do sleep 5; done

# Only what the build and the service need. The database goes once; rsync skips it after that.
rsync -az -e "ssh -i $KEY" --delete --exclude __pycache__ \
  porter Dockerfile requirements.txt compose.prod.yaml Caddyfile online_retail.db \
  "ubuntu@$PUBLIC_IP:porter/"
# The secrets go separately, and only into .env on the server — never into the image.
scp -i "$KEY" .env.prod "ubuntu@$PUBLIC_IP:porter/.env"
"${SSH[@]}" "grep -q '^SITE_ADDRESS=' porter/.env || echo 'SITE_ADDRESS=$SITE_ADDRESS' >> porter/.env"

"${SSH[@]}" "cd porter && PORTER_TAG=$TAG docker compose -f compose.prod.yaml up -d --build && docker image ls porter"
echo "deployed $TAG → https://$SITE_ADDRESS/version"
