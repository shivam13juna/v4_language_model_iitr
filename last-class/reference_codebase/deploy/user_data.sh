#!/bin/bash
# Runs once, as root, at the instance's first boot: Docker, its compose and buildx plugins (buildx
# builds the two-stage Dockerfile), and rsync.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 docker-buildx rsync
systemctl enable --now docker
usermod -aG docker ubuntu              # the ubuntu user, which push.sh logs in as, may use Docker
# 2 GB of swap: building the image on a 2 GB machine can run out of memory without it.
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
touch /var/lib/cloud/porter-ready      # push.sh waits for this file
