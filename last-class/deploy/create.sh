#!/usr/bin/env bash
# One EC2 instance for Porter. Safe to run again: it reuses what exists, and refreshes .state.
#
#   ./deploy/create.sh
#
# What it makes, in your default VPC:
#   a key pair         porter-demo   the only way in by ssh: ~/.ssh/porter-demo.pem
#   a security group   porter-demo   the instance's firewall: port 22 (ssh) from this laptop's
#                                    address only, port 8080 (Porter) from anywhere
#   an instance        t4g.small     2 vCPUs, 2 GB, arm64, Ubuntu 24.04; Docker goes on at first boot
#
# It costs money while it exists, about two US cents an hour. ./deploy/destroy.sh removes all three.
set -euo pipefail
cd "$(dirname "$0")"
export AWS_PAGER=""
# The region: AWS_REGION if set, else the one `aws configure` saved, else us-west-2.
REGION="${AWS_REGION:-$(command aws configure get region 2>/dev/null || true)}"
REGION="${REGION:-us-west-2}"
NAME=porter-demo
KEY="$HOME/.ssh/$NAME.pem"
aws() { command aws --region "$REGION" "$@"; }

aws sts get-caller-identity >/dev/null || { echo "AWS credentials are not working: run 'aws configure' first."; exit 1; }

# The key pair: AWS keeps the public half, this laptop the private half.
if [ ! -f "$KEY" ]; then
  aws ec2 create-key-pair --key-name $NAME --query KeyMaterial --output text > "$KEY"
  chmod 600 "$KEY"
fi

# The security group, and its two open ports. A port that is already open is left as it is; a
# laptop on a new network gets its new address added for ssh.
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=$NAME --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG" = "None" ]; then
  SG=$(aws ec2 create-security-group --group-name $NAME --description "Porter demo" --query GroupId --output text)
fi
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 \
  --cidr "$(curl -s https://checkip.amazonaws.com)/32" >/dev/null 2>&1 || true
aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 8080 \
  --cidr 0.0.0.0/0 >/dev/null 2>&1 || true

# The instance. user_data.sh runs once, as root, at its first boot.
ID=$(aws ec2 describe-instances --filters Name=tag:Name,Values=$NAME Name=instance-state-name,Values=pending,running,stopped \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [ "$ID" = "None" ]; then
  # The newest Ubuntu 24.04 for arm64: looked up, because image ids differ from region to region.
  AMI=$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
    --query Parameter.Value --output text)
  ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type t4g.small --key-name $NAME \
    --security-group-ids "$SG" --user-data file://user_data.sh \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":20}}]' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
    --query 'Instances[0].InstanceId' --output text)
fi
aws ec2 start-instances --instance-ids "$ID" >/dev/null 2>&1 || true
aws ec2 wait instance-running --instance-ids "$ID"
IP=$(aws ec2 describe-instances --instance-ids "$ID" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

# What the other scripts read. The public address changes if the instance is stopped and started.
printf 'REGION=%s\nINSTANCE_ID=%s\nSECURITY_GROUP=%s\nPUBLIC_IP=%s\n' "$REGION" "$ID" "$SG" "$IP" > .state
echo "instance $ID at $IP"
echo "Porter will answer at http://$IP:8080 once ./deploy/push.sh has run"
