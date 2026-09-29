#!/bin/bash
# Runs once, as root, when the instance first boots: Docker and the compose plugin, and the
# ubuntu user allowed to use them. Everything after this is `docker compose`.
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 rsync
systemctl enable --now docker
usermod -aG docker ubuntu
# A small instance building Python wheels can run out of memory; 2 GB of swap is cheap insurance.
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
touch /var/lib/cloud/porter-ready
