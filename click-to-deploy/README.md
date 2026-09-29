# Claude Code + Vertex AI Hybrid Multi-Agent Demo — Click-to-Deploy

This directory contains the 2-stage Terraform Click-to-Deploy package for `go/demos` (Demospace) and Google Cloud Argolis sandboxes.

## What Gets Provisioned
1. **Stage 1 (`org_policy/`)**:
   - Enables `aiplatform.googleapis.com`, `compute.googleapis.com`, `iap.googleapis.com`, `iam.googleapis.com`, `cloudresourcemanager.googleapis.com`, `serviceusage.googleapis.com`, and `logging.googleapis.com`.
   - Relaxes sandbox Org Policies required for VM provisioning.
2. **Stage 2 (`demo/terraform/`)**:
   - Dedicated least-privilege Service Account (`claude-code-vertex-sa`) granted `roles/aiplatform.user` and `roles/logging.logWriter`.
   - Isolated VPC (`claude-vertex-demo-vpc`), Subnet (`10.20.0.0/24`), Cloud Router + Cloud NAT, and IAP SSH Firewall (`35.235.240.0/20` on TCP port `22`).
   - Pre-warmed Ubuntu 24.04 LTS GCE Workstation (`claude-code-vertex-demo`, `e2-standard-4`) with Node.js 22, `@anthropic-ai/claude-code`, `tmux`, hash-pinned Python dependencies, and the `litellm-vertex-gateway.service` systemd daemon listening on `127.0.0.1:4000`.

## One-Command Deployment
```bash
./quickstart-deploy.sh <YOUR_GCP_PROJECT_ID> us-central1 us-central1-a
```

## Connecting & Running the Demo
```bash
gcloud compute ssh claude-code-vertex-demo --project=<YOUR_GCP_PROJECT_ID> --zone=us-central1-a --tunnel-through-iap
cd /opt/claude-code-with-gemini
./small.sh
```
