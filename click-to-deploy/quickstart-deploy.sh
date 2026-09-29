#!/usr/bin/env bash
# ==============================================================================
# Click-to-Deploy Quickstart Orchestrator for Claude Code + Vertex AI Hybrid Demo
# Follows the 2-phase go/demos Click-to-Deploy pattern:
#   Phase 1: Apply org_policy overrides & enable required GCP APIs (sleep 45s)
#   Phase 2: Provision IAM, VPC/NAT, RDP (3389) + IAP SSH (22) firewall, and GCE VM
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ID="${1:-${GOOGLE_CLOUD_PROJECT:-}}"
REGION="${2:-us-central1}"
ZONE="${3:-us-central1-a}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "Usage: $0 <GCP_PROJECT_ID> [REGION] [ZONE]" >&2
  exit 1
fi

echo "======================================================================"
echo "  Deploying Claude Code + Vertex AI Hybrid Demo to ${PROJECT_ID}"
echo "  Region: ${REGION} | Zone: ${ZONE}"
echo "======================================================================"

echo "[Phase 1/2] Applying Organization Policy overrides and enabling APIs..."
pushd "${SCRIPT_DIR}/org_policy" >/dev/null
terraform init -input=false
terraform apply -auto-approve -var="project_id=${PROJECT_ID}"
popd >/dev/null

echo "Waiting 45 seconds for Org Policy and API propagation..."
sleep 45

echo "[Phase 2/2] Provisioning VPC, Secret Manager RDP Password, Firewall, and Demo VM..."
pushd "${SCRIPT_DIR}/demo/terraform" >/dev/null
terraform init -input=false
terraform apply -auto-approve \
  -var="project_id=${PROJECT_ID}" \
  -var="region=${REGION}" \
  -var="zone=${ZONE}"

echo ""
echo "======================================================================"
echo "  Deployment Complete! Connect via RDP (Port 3389) or IAP SSH:"
echo "======================================================================"
echo "  RDP Connection : $(terraform output -raw rdp_external_ip):3389"
echo "  RDP Username   : $(terraform output -raw rdp_username)"
echo "  RDP Password   : $(terraform output -raw rdp_password)"
echo "  IAP SSH Command: $(terraform output -raw ssh_command)"
echo "======================================================================"
popd >/dev/null
