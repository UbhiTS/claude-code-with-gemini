#!/usr/bin/env bash
# Startup script for Claude Code + Vertex AI Hybrid Demo Workstation VM
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y curl git jq python3 python3-venv python3-pip tmux ca-certificates gnupg

# Install Node.js 22 LTS & Claude Code CLI (@anthropic-ai/claude-code)
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi
npm install -g @anthropic-ai/claude-code

# Clone or update the demo repository into /opt/claude-code-with-gemini
DEMO_DIR="/opt/claude-code-with-gemini"
if [[ ! -d "${DEMO_DIR}" ]]; then
  git clone https://github.com/UbhiTS/claude-code-with-gemini.git "${DEMO_DIR}"
else
  git -C "${DEMO_DIR}" pull --ff-only || true
fi
chmod -R a+rwx "${DEMO_DIR}"

# Install hash-pinned Python dependencies per go/pip-install-remediation (b/391732366)
python3 -m venv "${DEMO_DIR}/.venv"
"${DEMO_DIR}/.venv/bin/pip" install --upgrade pip
"${DEMO_DIR}/.venv/bin/pip" install --require-hashes -r "${DEMO_DIR}/requirements.txt"
ln -sf "${DEMO_DIR}/.venv/bin/pytest" /usr/local/bin/pytest

# Resolve GCP Project ID from GCE Metadata Server
PROJECT_ID="$(curl -s -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/project/project-id || echo 'llm-compare-ubhits')"
sed -i "s/^VERTEX_PROJECT_ID=.*/VERTEX_PROJECT_ID=${PROJECT_ID}/" "${DEMO_DIR}/config/models.env" || true

# Configure systemd service for LiteLLM Vertex AI Hybrid Gateway on 127.0.0.1:4000
cat > /etc/systemd/system/litellm-vertex-gateway.service <<EOF
[Unit]
Description=LiteLLM Vertex AI Hybrid Gateway for Claude Code
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${DEMO_DIR}
Environment="VERTEX_PROJECT_ID=${PROJECT_ID}"
Environment="GATEWAY_HOST=127.0.0.1"
Environment="GATEWAY_PORT=4000"
ExecStart=${DEMO_DIR}/.venv/bin/python3 ${DEMO_DIR}/src/litellm_vertex_gateway.py --host 127.0.0.1 --port 4000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now litellm-vertex-gateway.service

# Configure SSH login banner & environment in /etc/profile.d/claude-vertex-demo.sh
cat > /etc/profile.d/claude-vertex-demo.sh <<'EOF'
export ANTHROPIC_BASE_URL="http://127.0.0.1:4000"
export ANTHROPIC_API_KEY="sk-vertex-hybrid-demo"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1"
export CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT="1"
export PATH="/opt/claude-code-with-gemini/.venv/bin:/usr/local/bin:${PATH}"

if [[ -t 1 ]]; then
  echo "================================================================================"
  echo "  Welcome to the Claude Code + Vertex AI Hybrid Multi-Agent Demo Workstation!"
  echo "  LiteLLM Vertex AI Gateway : http://127.0.0.1:4000 (systemd active)"
  echo "  Demo Repository           : /opt/claude-code-with-gemini"
  echo ""
  echo "  Quickstart Commands:"
  echo "    cd /opt/claude-code-with-gemini"
  echo "    ./small.sh               # Run Small 3-Agent Benchmark (Opus 5.5 + Gemini 3.8 Flash + Sonnet 5)"
  echo "    ./medium.sh              # Run Medium 3-Agent Benchmark (Payment Microservice)"
  echo "    ./large.sh               # Run Large 3-Agent Benchmark (Cloud FinOps Platform)"
  echo "    ./cost-report.sh         # View Live Telemetry & Counterfactual What-If Economics"
  echo "    ./what-if.sh --help      # Simulate Any Planner/Implementer/Reviewer Model Combination"
  echo "    ./configure-models.sh    # Switch Active Agent Models"
  echo "================================================================================"
fi
EOF
chmod +x /etc/profile.d/claude-vertex-demo.sh
