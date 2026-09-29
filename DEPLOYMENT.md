# Deployment & Customer PoC Guide (`go/demos` & Argolis)

This guide covers deploying **Claude Code + Gemini 3.8 Flash on Vertex AI (via LiteLLM)** either onto a pre-warmed Google Cloud Compute Engine workstation with **Remote Desktop (RDP port `3389`) + IAP SSH (`click-to-deploy/`)** or locally on any Cloudtop / macOS / Linux developer machine.

---

## Option 1: Automated 1-Click Terraform Deployment (`click-to-deploy/`)

Designed for Google Cloud Customer Engineers (`go/demos` / Argolis sandboxes) to spin up a turnkey **Ubuntu 22.04 LTS** graphical + terminal demo workstation with:
- **Open RDP Access (`TCP 3389`)**: `xrdp` + `XFCE4` desktop environment + **Visual Studio Code (`code`)** + `code-server`.
- **Auto-Generated Secret Manager Password**: Stored securely in Secret Manager (`claude-vertex-rdp-password`) and printed at the end of `quickstart-deploy.sh`.
- **5 Pre-Built XFCE4 Desktop Icons**:
  1. `1. Run Small Demo (Rate Limiter)` — opens maximized `xfce4-terminal` with 3-pane `tmux` dashboard
  2. `2. Run Medium Demo (Payment Microservice)`
  3. `3. Run Large Demo (Cloud FinOps Platform)`
  4. `4. Cost & What-If Report` — renders terminal & Firefox HTML executive dashboard
  5. `5. Open in VS Code` — opens `/opt/claude-code-with-gemini` in VS Code

```bash
cd click-to-deploy
chmod +x quickstart-deploy.sh
./quickstart-deploy.sh <YOUR_GCP_PROJECT_ID> us-central1 us-central1-a
```

### Connect via Remote Desktop (RDP) or IAP SSH
1. **Remote Desktop (RDP — Port `3389`)**:
   - **Host**: `<rdp_external_ip>:3389` (printed by `terraform output -raw rdp_external_ip`)
   - **Username**: `demo`
   - **Password**: Retrieve anytime with:
     ```bash
     gcloud secrets versions access latest --secret=claude-vertex-rdp-password --project=<YOUR_GCP_PROJECT_ID>
     ```
2. **Direct Terminal via IAP SSH**:
   ```bash
   gcloud compute ssh claude-code-vertex-demo \
     --project=<YOUR_GCP_PROJECT_ID> \
     --zone=us-central1-a \
     --tunnel-through-iap

   cd /opt/claude-code-with-gemini
   ./small.sh
   ```

---

## Option 2: Local Workstation / Cloudtop Setup

### 1. Install Node.js 22+ & Official Claude Code CLI
```bash
npm install -g @anthropic-ai/claude-code
claude --version
```

### 2. Install Hash-Pinned Python Dependencies (`go/pip-install-remediation`)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.txt
```

### 3. Configure Vertex AI Project & Credentials
Authenticate with Google Cloud Application Default Credentials (or set `AGENT_PLATFORM_API_KEY` in `.env`):
```bash
gcloud auth application-default login
export VERTEX_PROJECT_ID="your-gcp-project-id"
```

### 4. Run the 3-Stage Benchmarks & What-If Simulator
```bash
# Run the Small task (Token-Bucket Rate Limiter & Tiered Burst CLI)
./small.sh

# Run the Medium task (Payment & Order Fulfillment Microservice)
./medium.sh

# Run the Large task (Cloud FinOps Anomaly Detector & Dashboard)
./large.sh

# View the Executive Cost & Speed Report + HTML Dashboard
./cost-report.sh

# Run Counterfactual What-If Simulations for any model combination
./what-if.sh --planner claude-opus-5-5 --implementer claude-sonnet-5 --reviewer claude-sonnet-5 --devs 250
```

---

## Teardown & Cost Hygiene
To tear down the Compute Engine workstation and networking resources after a customer demo:
```bash
cd click-to-deploy/demo/terraform
terraform destroy -auto-approve -var="project_id=<YOUR_GCP_PROJECT_ID>"
```
