#!/usr/bin/env bash
# Put this machine's Ollama on a public URL, behind a token. Run it in a terminal and leave
# it running; Ctrl+C closes the tunnel.
#
#   ./ngrok/start_model_tunnel.sh
#
# Reads PORTER_MODEL_TOKEN and NGROK_DOMAIN from .env (see ngrok/README.md for both).
set -euo pipefail
cd "$(dirname "$0")/.."

set -a; source .env; set +a
: "${PORTER_MODEL_TOKEN:?set PORTER_MODEL_TOKEN in .env}"
: "${NGROK_DOMAIN:?set NGROK_DOMAIN in .env (your free static domain, e.g. something.ngrok-free.app)}"

policy="$(mktemp -t porter-tunnel).yml"
trap 'rm -f "$policy"' EXIT
python3 - "$policy" <<'PY'
import os, sys
text = open("ngrok/model-tunnel.template.yml").read().replace("${PORTER_MODEL_TOKEN}", os.environ["PORTER_MODEL_TOKEN"])
open(sys.argv[1], "w").write(text)
PY

echo "tunnel:  https://${NGROK_DOMAIN}/v1  →  http://127.0.0.1:11434/v1"
echo "callers set PORTER_BASE_URL=https://${NGROK_DOMAIN}/v1 and PORTER_API_KEY=<the token>"
# Ollama refuses any Host header but its own (HTTP 403), so the tunnel rewrites it.
exec ngrok http 11434 --url "https://${NGROK_DOMAIN}" --host-header=localhost:11434 \
     --traffic-policy-file "$policy"
