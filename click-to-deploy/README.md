# Claude Code + Vertex AI Hybrid Multi-Agent Demo — Click-to-Deploy

This directory contains the 2-stage Terraform Click-to-Deploy package for `go/demos` (Demospace) and Google Cloud Argolis sandboxes.

## What Gets Provisioned
1. **Stage 1 (`org_policy/`)**:
   - Enables `orgpolicy.googleapis.com`, `serviceusage.googleapis.com`, `aiplatform.googleapis.com`, `compute.googleapis.com`, `secretmanager.googleapis.com`, `iap.googleapis.com`, `iam.googleapis.com`, `cloudresourcemanager.googleapis.com`, and `logging.googleapis.com`.
   - Relaxes sandbox Org Policies (`compute.vmExternalIpAccess`, `iam.allowedPolicyMemberDomains`, `compute.requireOsLogin`, `compute.requireShieldedVm`) required for external RDP VM provisioning.
2. **Stage 2 (`demo/terraform/`)**:
   - Dedicated least-privilege Service Account (`claude-code-vertex-sa`) granted `roles/aiplatform.user`, `roles/secretmanager.secretAccessor`, and `roles/logging.logWriter`.
   - Auto-generated 16-character RDP password stored in Secret Manager (`claude-vertex-rdp-password`).
   - Isolated VPC (`claude-vertex-demo-vpc`), Subnet (`10.20.0.0/24`), Cloud Router + Cloud NAT, Open RDP Firewall (`0.0.0.0/0` on TCP port `3389`), and IAP SSH Firewall (`35.235.240.0/20` on TCP port `22`).
   - Pre-warmed Ubuntu 22.04 LTS GCE Workstation (`claude-code-vertex-demo`, `e2-standard-4`) with External IP, `xrdp` + `XFCE4` desktop, Visual Studio Code (`code`), Node.js 22, `@anthropic-ai/claude-code`, `tmux`, hash-pinned Python dependencies, 5 Desktop launchers, and the `litellm-vertex-gateway.service` systemd daemon listening on `127.0.0.1:4000`.

## One-Command Deployment
```bash
./quickstart-deploy.sh <YOUR_GCP_PROJECT_ID> us-central1 us-central1-a
```

## Connecting via RDP (`3389`) or IAP SSH (`22`)
```bash
# Retrieve RDP External IP & Password
cd demo/terraform
echo "RDP Host: $(terraform output -raw rdp_external_ip):3389 (User: demo)"
terraform output -raw rdp_password

# Or connect via IAP SSH
gcloud compute ssh claude-code-vertex-demo --project=<YOUR_GCP_PROJECT_ID> --zone=us-central1-a --tunnel-through-iap
cd /opt/claude-code-with-gemini
./small.sh
```
