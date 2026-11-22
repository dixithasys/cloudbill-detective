#!/usr/bin/env bash
# ==============================================================================
# scripts/teardown.sh
# Tears down the tiny test resources created by scripts/plant_waste.sh:
#   - Terminates the t3.micro EC2 instance
#   - Releases the unassociated Elastic IP
#   - Deletes the 1 GB unattached EBS volume
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_FILE="${SCRIPT_DIR}/.planted_waste.env"
TAG_KEY="Project"
TAG_VALUE="cloudbill-detective-test"

if [[ -f "${STATE_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${STATE_FILE}"
else
  REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
  echo "[cloudbill-detective] ${STATE_FILE} not found; looking up resources by tag ${TAG_KEY}=${TAG_VALUE} in ${REGION}..."

  INSTANCE_ID="$(aws ec2 describe-instances \
    --region "${REGION}" \
    --filters "Name=tag:${TAG_KEY},Values=${TAG_VALUE}" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query "Reservations[].Instances[].InstanceId" \
    --output text)"

  ALLOCATION_ID="$(aws ec2 describe-addresses \
    --region "${REGION}" \
    --filters "Name=tag:${TAG_KEY},Values=${TAG_VALUE}" \
    --query "Addresses[].AllocationId" \
    --output text)"

  VOLUME_ID="$(aws ec2 describe-volumes \
    --region "${REGION}" \
    --filters "Name=tag:${TAG_KEY},Values=${TAG_VALUE}" \
    --query "Volumes[].VolumeId" \
    --output text)"
fi

echo "[cloudbill-detective] Tearing down planted resources in region: ${REGION}"

if [[ -n "${INSTANCE_ID:-}" ]]; then
  echo "1/3 Terminating EC2 instance(s): ${INSTANCE_ID}..."
  # shellcheck disable=SC2086
  aws ec2 terminate-instances --region "${REGION}" --instance-ids ${INSTANCE_ID} >/dev/null
  echo "    Waiting for instance(s) to terminate..."
  # shellcheck disable=SC2086
  aws ec2 wait instance-terminated --region "${REGION}" --instance-ids ${INSTANCE_ID}
  echo "    Terminated: ${INSTANCE_ID}"
else
  echo "1/3 No planted EC2 instance found."
fi

if [[ -n "${ALLOCATION_ID:-}" ]]; then
  for alloc in ${ALLOCATION_ID}; do
    echo "2/3 Releasing Elastic IP AllocationId: ${alloc}..."
    aws ec2 release-address --region "${REGION}" --allocation-id "${alloc}"
    echo "    Released: ${alloc}"
  done
else
  echo "2/3 No planted Elastic IP found."
fi

if [[ -n "${VOLUME_ID:-}" ]]; then
  for vol in ${VOLUME_ID}; do
    echo "3/3 Deleting 1 GB EBS VolumeId: ${vol}..."
    aws ec2 delete-volume --region "${REGION}" --volume-id "${vol}"
    echo "    Deleted: ${vol}"
  done
else
  echo "3/3 No planted EBS volume found."
fi

if [[ -f "${STATE_FILE}" ]]; then
  rm -f "${STATE_FILE}"
  echo "Removed state file ${STATE_FILE}."
fi

echo "[cloudbill-detective] Teardown complete."

