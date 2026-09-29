#!/usr/bin/env bash
# The release gate. Nothing ships that has not passed it, and it tests the exact image that
# ships — not the source on your laptop.
#
#   ./release.sh 2.1.0              build, gate, and record the version as releasable
#   ./release.sh 2.1.0 --full       also run the regression + held-out suites (slow)
#   ./release.sh 2.1.0 --deploy     then push it to the EC2 instance (deploy/aws/push.sh)
#
# Every check runs in a fresh container that exits when it is done — no server is started:
#   1  the image imports and can reach everything it needs   (python -m porter.selfcheck)
#   2  the smoke suite, against the app in-process           (python -m porter.evals)
#   3  (--full) golden and held-out, same way
# The gate asks the model .env names (OpenAI by default, or this machine's Ollama with
# PORTER_PROVIDER=ollama); it writes to scratch state, never to the real ledger.
set -euo pipefail
cd "$(dirname "$0")"
# .env's values become this script's environment, so the gate uses the laptop's model settings.
set -a; [ -f .env ] && . ./.env; set +a

TAG="${1:?usage: ./release.sh <version> [--full] [--deploy]}"
FULL=false; DEPLOY=false
for arg in "${@:2}"; do
  case "$arg" in --full) FULL=true ;; --deploy) DEPLOY=true ;; esac
done
say() { printf '\n\033[1m── %s\033[0m\n' "$1"; }

say "build porter:$TAG"
docker build --build-arg PORTER_VERSION="$TAG" -t "porter:$TAG" .

# The model settings go in by name only (-e NAME): docker copies each value from this script's
# environment, so the OpenAI key never appears on a command line. PORTER_OLLAMA_URL is where
# a container finds this machine's Ollama, if the provider is ollama.
RUN=(docker run --rm
     --add-host host.docker.internal:host-gateway
     -e PORTER_PROVIDER -e PORTER_MODEL -e OPENAI_API_KEY -e PORTER_BASE_URL -e PORTER_API_KEY
     -e PORTER_OLLAMA_URL=http://host.docker.internal:11434/v1
     -e PORTER_AUTH_SECRET="gate-only-secret-not-used-anywhere-else-000"
     -e PORTER_CHECKPOINT_PATH=/tmp/threads.sqlite
     -e PORTER_LEDGER_PATH=/tmp/ledger.sqlite
     -e PORTER_JSON_LOGS=false -e PORTER_LOG_LEVEL=warning
     -v "$PWD/online_retail.db:/data/online_retail.db:ro"
     -v "$PWD/evals:/app/evals:ro"
     -v "$PWD/results:/app/results"
     -u "$(id -u):$(id -g)"
     "porter:$TAG")

say "1 · selfcheck, in a fresh container"
"${RUN[@]}" python -m porter.selfcheck

say "2 · smoke suite, inside the candidate image"
"${RUN[@]}" python -m porter.evals --suite smoke --in-process --label "$TAG" \
  --save "results/release_${TAG}_smoke.json"

if $FULL; then
  for suite in golden heldout; do
    say "3 · $suite suite"
    "${RUN[@]}" python -m porter.evals --suite "$suite" --in-process --label "$TAG" \
      --save "results/release_${TAG}_${suite}.json"
  done
fi

echo "$TAG $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> results/RELEASED
say "PASSED — porter:$TAG is releasable"

if $DEPLOY; then
  say "deploy $TAG"
  ./deploy/aws/push.sh "$TAG"
fi
