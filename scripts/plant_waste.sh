#!/usr/bin/env bash
# ==============================================================================
# scripts/plant_waste.sh
# Plants tiny test resources in AWS for live testing of cloudbill-detective:
#   1. 1 GB unattached gp3 EBS volume (~$0.08/month)
#   2. Unassociated Elastic IP (~$3.65/month, $0.005/hour)
#   3. Idle t3.micro EC2 instance (~$7.59/month, $0.0104/hour; Free Tier eligible)
#
# All created resources are tagged with Project=cloudbill-detective-test
# and their IDs are recorded in scripts/.planted_waste.env for teardown.sh.
#
# NOTE: Review before running! Always run scripts/teardown.sh when finished.
# ==============================================================================
set -euo pipefail

REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
TAG_KEY="Project"
TAG_VALUE="cloudbill-detective-test"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${SCRIPT_DIR}/.planted_waste.env"

echo "[cloudbill-detective] Planting tiny test resources in region: ${REGION}"

# Determine first available AZ in the region for the 1GB EBS volume
AZ="$(aws ec2 describe-availability-zones \
  --region "${REGION}" \
  --filters "Name=state,Values=available" \
  --query "AvailabilityZones[0].ZoneName" \
  --output text)"

echo "1/3 Creating unattached 1 GB gp3 EBS volume in ${AZ}..."
VOLUME_ID="$(aws ec2 create-volume \
  --region "${REGION}" \
  --availability-zone "${AZ}" \
  --size 1 \
  --volume-type gp3 \
  --tag-specifications "ResourceType=volume,Tags=[{Key=${TAG_KEY},Value=${TAG_VALUE}},{Key=Name,Value=cloudbill-detective-test-1gb}]" \
  --query "VolumeId" \
  --output text)"
echo "    Created VolumeId: ${VOLUME_ID}"

echo "2/3 Allocating unassociated Elastic IP..."
ALLOCATION_ID="$(aws ec2 allocate-address \
  --region "${REGION}" \
  --domain vpc \
  --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=${TAG_KEY},Value=${TAG_VALUE}},{Key=Name,Value=cloudbill-detective-test-eip}]" \
  --query "AllocationId" \
  --output text)"
echo "    Allocated Elastic IP AllocationId: ${ALLOCATION_ID}"

echo "3/3 Launching tiny idle t3.micro EC2 instance (Amazon Linux 2023)..."
AMI_ID="$(aws ssm get-parameters \
  --region "${REGION}" \
  --names /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64 \
  --query "Parameters[0].Value" \
  --output text)"

INSTANCE_ID="$(aws ec2 run-instances \
  --region "${REGION}" \
  --image-id "${AMI_ID}" \
  --instance-type t3.micro \
  --count 1 \
  --tag-specifications "ResourceType=instance,Tags=[{Key=${TAG_KEY},Value=${TAG_VALUE}},{Key=Name,Value=cloudbill-detective-test-t3micro}]" \
  --query "Instances[0].InstanceId" \
  --output text)"
echo "    Launched InstanceId: ${INSTANCE_ID}"

cat > "${STATE_FILE}" <<EOF
REGION=${REGION}
VOLUME_ID=${VOLUME_ID}
ALLOCATION_ID=${ALLOCATION_ID}
INSTANCE_ID=${INSTANCE_ID}
EOF

echo ""
echo "[cloudbill-detective] Planted resources saved to ${STATE_FILE}:"
cat "${STATE_FILE}"
echo ""
echo "IMPORTANT: Run './scripts/teardown.sh' when finished testing to avoid ongoing AWS charges."

