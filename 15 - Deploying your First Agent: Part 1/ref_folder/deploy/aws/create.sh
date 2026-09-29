#!/usr/bin/env bash
# Create the one EC2 instance Porter runs on. Run once; safe to re-run (it reuses what exists).
#
#   ./deploy/aws/create.sh
#
# What it makes, in your default VPC:
#   a key pair            porter-demo  → ~/.ssh/porter-demo.pem
#   a security group      porter-demo  → 22 from your IP only; 80 and 443 from anywhere
#   an instance           t4g.small (2 vCPU, 2 GB, Graviton/arm64), Ubuntu 24.04, 20 GB disk
#
# Costs money while it exists (a t4g.small is roughly two cents an hour, plus the public
# IPv4 address and the disk). ./deploy/aws/destroy.sh removes all three.
set -euo pipefail
cd "$(dirname "$0")"

REGION="${AWS_REGION:-us-west-2}"
TYPE="${INSTANCE_TYPE:-t4g.small}"
NAME=porter-demo
KEY_FILE="$HOME/.ssh/${NAME}.pem"
aws() { command aws --region "$REGION" "$@"; }

aws sts get-caller-identity --query Arn --output text >/dev/null \
  || { echo "AWS credentials are not working — run 'aws configure' (or 'aws sso login') first."; exit 1; }

# The newest Ubuntu 24.04 image for arm64, looked up rather than hard-coded: AMI ids differ per
# region and change every few weeks.
AMI=$(aws ssm get-parameter \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)

if [ ! -f "$KEY_FILE" ]; then
  aws ec2 create-key-pair --key-name "$NAME" --query KeyMaterial --output text > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
fi

VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values="$NAME" Name=vpc-id,Values="$VPC" \
       --query 'SecurityGroups[0].GroupId' --output text)
if [ "$SG" = "None" ]; then
  SG=$(aws ec2 create-security-group --group-name "$NAME" --vpc-id "$VPC" \
         --description "Porter: ssh from one address, web from anywhere" --query GroupId --output text)
  MYIP=$(curl -s https://checkip.amazonaws.com)
  aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 22 --cidr "${MYIP}/32" >/dev/null
  aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 80 --cidr 0.0.0.0/0 >/dev/null
  aws ec2 authorize-security-group-ingress --group-id "$SG" --protocol tcp --port 443 --cidr 0.0.0.0/0 >/dev/null
fi

ID=$(aws ec2 describe-instances --filters Name=tag:Name,Values="$NAME" Name=instance-state-name,Values=pending,running,stopped \
       --query 'Reservations[0].Instances[0].InstanceId' --output text)
if [ "$ID" = "None" ]; then
  ID=$(aws ec2 run-instances --image-id "$AMI" --instance-type "$TYPE" --key-name "$NAME" \
         --security-group-ids "$SG" --user-data file://user_data.sh \
         --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":20,"VolumeType":"gp3"}}]' \
         --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
         --query 'Instances[0].InstanceId' --output text)
fi
aws ec2 start-instances --instance-ids "$ID" >/dev/null 2>&1 || true
aws ec2 wait instance-running --instance-ids "$ID"
IP=$(aws ec2 describe-instances --instance-ids "$ID" --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

cat > .state <<STATE
REGION=$REGION
INSTANCE_ID=$ID
SECURITY_GROUP=$SG
PUBLIC_IP=$IP
SITE_ADDRESS=${IP//./-}.sslip.io
STATE
echo "instance  $ID  $IP"
echo "site      https://${IP//./-}.sslip.io   (once ./deploy/aws/push.sh has run)"
echo "ssh       ssh -i $KEY_FILE ubuntu@$IP"
echo "note      the public IP changes if the instance is stopped and started; re-run this to refresh .state"
