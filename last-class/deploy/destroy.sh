#!/usr/bin/env bash
# Remove everything create.sh made: the instance and its disk, the security group, the key pair.
set -euo pipefail
cd "$(dirname "$0")"
source .state
export AWS_PAGER=""
aws() { command aws --region "$REGION" "$@"; }
aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" >/dev/null
aws ec2 wait instance-terminated --instance-ids "$INSTANCE_ID"
aws ec2 delete-security-group --group-id "$SECURITY_GROUP"
aws ec2 delete-key-pair --key-name porter-demo
rm -f "$HOME/.ssh/porter-demo.pem" .state
echo "gone"
