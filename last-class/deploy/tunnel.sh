#!/usr/bin/env bash
# Prometheus and Grafana on the instance, opened on this laptop. Leave it running; Ctrl+C closes it.
#
#   ./deploy/tunnel.sh      then http://localhost:9090 (Prometheus) and http://localhost:3000 (Grafana)
#
# Both listen only on the instance's own address (compose.yaml), and the security group lets in
# nothing but 22 and 8080. ssh carries them: each -L sends a port on this laptop to a port on the
# instance, inside the ssh connection.
set -euo pipefail
cd "$(dirname "$0")"
source .state
echo "Prometheus http://localhost:9090 · Grafana http://localhost:3000 · Ctrl+C to close"
exec ssh -i "$HOME/.ssh/porter-demo.pem" -N -o ExitOnForwardFailure=yes \
  -L 9090:127.0.0.1:9090 -L 3000:127.0.0.1:3000 "ubuntu@$PUBLIC_IP"
