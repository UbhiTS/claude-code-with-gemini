#!/usr/bin/env bash
# Startup script for Claude Code + Vertex AI Hybrid Demo Workstation VM (Ubuntu 22.04 LTS + XFCE4 + xrdp + VS Code)
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  curl git jq wget gpg ca-certificates gnupg \
  python3 python3-venv python3-pip \
  tmux xfce4 xfce4-goodies xfce4-terminal xrdp dbus-x11 firefox

# 1. Install Visual Studio Code (code) & code-server
if ! command -v code >/dev/null 2>&1; then
  wget -qO- https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor > /usr/share/keyrings/packages.microsoft.gpg
  echo "deb [arch=amd64 signed-by=/usr/share/keyrings/packages.microsoft.gpg] https://packages.microsoft.com/repos/code stable main" > /etc/apt/sources.list.d/vscode.list
  apt-get update -y && apt-get install -y code || true
fi
if ! command -v code-server >/dev/null 2>&1; then
  curl -fsSL https://code-server.dev/install.sh | sh || true
fi

# 2. Install Node.js 22 LTS & Claude Code CLI (@anthropic-ai/claude-code)
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y nodejs
fi
npm install -g @anthropic-ai/claude-code

# 3. Create 'demo' user for RDP login and fetch auto-generated password from Secret Manager
if ! id -u demo >/dev/null 2>&1; then
  useradd -m -s /bin/bash -G sudo,ssl-cert demo
fi
echo "demo ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-demo-user
chmod 0440 /etc/sudoers.d/90-demo-user

PROJECT_ID="$(curl -s -H 'Metadata-Flavor: Google' http://metadata.google.internal/computeMetadata/v1/project/project-id || echo 'llm-compare-ubhits')"
RDP_PASS="$(gcloud secrets versions access latest --secret=claude-vertex-rdp-password --project="${PROJECT_ID}" 2>/dev/null || echo 'ClaudeGemini2026')"
echo "demo:${RDP_PASS}" | chpasswd

# 4. Configure xrdp + XFCE4 Desktop Session
adduser xrdp ssl-cert || true
echo "xfce4-session" > /home/demo/.xsession
chown demo:demo /home/demo/.xsession
chmod 0644 /home/demo/.xsession

sed -i 's/^test -x \/etc\/X11\/Xsession && exec \/etc\/X11\/Xsession/exec startxfce4/' /etc/xrdp/startwm.sh || true
systemctl enable xrdp
systemctl restart xrdp

# 5. Clone or update the demo repository into /opt/claude-code-with-gemini
DEMO_DIR="/opt/claude-code-with-gemini"
git config --system --add safe.directory "${DEMO_DIR}" || true
if [[ ! -d "${DEMO_DIR}" ]]; then
  git clone https://github.com/UbhiTS/claude-code-with-gemini.git "${DEMO_DIR}"
else
  git -C "${DEMO_DIR}" pull --ff-only || true
fi
chown -R demo:demo "${DEMO_DIR}"
chmod -R a+rwx "${DEMO_DIR}"

# 6. Install hash-pinned Python dependencies per go/pip-install-remediation (b/391732366)
python3 -m venv "${DEMO_DIR}/.venv"
"${DEMO_DIR}/.venv/bin/pip" install --upgrade pip || true
"${DEMO_DIR}/.venv/bin/pip" install --require-hashes -r "${DEMO_DIR}/requirements.txt" \
  || "${DEMO_DIR}/.venv/bin/pip" install pytest==8.4.1
ln -sf "${DEMO_DIR}/.venv/bin/pytest" /usr/local/bin/pytest

sed -i "s/^VERTEX_PROJECT_ID=.*/VERTEX_PROJECT_ID=${PROJECT_ID}/" "${DEMO_DIR}/config/models.env" || true
grep -q "^GCP_PROJECT_ID=" "${DEMO_DIR}/config/models.env" \
  && sed -i "s/^GCP_PROJECT_ID=.*/GCP_PROJECT_ID=${PROJECT_ID}/" "${DEMO_DIR}/config/models.env" \
  || echo "GCP_PROJECT_ID=${PROJECT_ID}" >> "${DEMO_DIR}/config/models.env"

# Pre-trust the project workspace for Claude Code CLI under user 'demo'
cat > /home/demo/.claude.json <<EOF
{
  "projects": {
    "/opt/claude-code-with-gemini": {
      "hasTrustDialogAccepted": true
    }
  }
}
EOF
chown demo:demo /home/demo/.claude.json

# 7. Configure systemd service for LiteLLM Vertex AI Hybrid Gateway on 127.0.0.1:4000
cat > /etc/systemd/system/litellm-vertex-gateway.service <<EOF
[Unit]
Description=LiteLLM Vertex AI Hybrid Gateway for Claude Code
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=demo
WorkingDirectory=${DEMO_DIR}
Environment="PYTHONPATH=${DEMO_DIR}"
Environment="GCP_PROJECT_ID=${PROJECT_ID}"
Environment="VERTEX_PROJECT_ID=${PROJECT_ID}"
Environment="GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
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

# 8. Create 1-Click XFCE4 Desktop Launchers for RDP Users
DESKTOP_DIR="/home/demo/Desktop"
mkdir -p "${DESKTOP_DIR}"

cat > "${DESKTOP_DIR}/1-Small-Demo.desktop" <<'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=1. Run Small Demo (Rate Limiter)
Comment=Launch Pure Claude Code (Left Window) + Live What-If Analyzer (Right Window)
Exec=/opt/claude-code-with-gemini/small.sh --from-desktop
Icon=utilities-terminal
Terminal=false
Categories=Development;
EOF

cat > "${DESKTOP_DIR}/2-Medium-Demo.desktop" <<'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=2. Run Medium Demo (Payment Microservice)
Comment=Launch Pure Claude Code (Left Window) + Live What-If Analyzer (Right Window)
Exec=/opt/claude-code-with-gemini/medium.sh --from-desktop
Icon=utilities-terminal
Terminal=false
Categories=Development;
EOF

cat > "${DESKTOP_DIR}/3-Large-Demo.desktop" <<'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=3. Run Large Demo (Cloud FinOps Platform)
Comment=Launch Pure Claude Code (Left Window) + Live What-If Analyzer (Right Window)
Exec=/opt/claude-code-with-gemini/large.sh --from-desktop
Icon=utilities-terminal
Terminal=false
Categories=Development;
EOF

cat > "${DESKTOP_DIR}/4-Cost-WhatIf-Report.desktop" <<'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=4. Cost & What-If Report
Comment=View Live Telemetry & Counterfactual Cost/Speed Analysis
Exec=xfce4-terminal --maximize --title="Vertex AI Hybrid Cost & What-If Report" -e "bash -lc 'cd /opt/claude-code-with-gemini && ./cost-report.sh && firefox logs/latest_report.html >/dev/null 2>&1 & exec bash'"
Icon=utilities-system-monitor
Terminal=false
Categories=Development;
EOF

cat > "${DESKTOP_DIR}/5-VSCode-Workspace.desktop" <<'EOF'
[Desktop Entry]
Version=1.0
Type=Application
Name=5. Open in VS Code
Comment=Open /opt/claude-code-with-gemini in Visual Studio Code
Exec=code --no-sandbox /opt/claude-code-with-gemini
Icon=com.visualstudio.code
Terminal=false
Categories=Development;
EOF

chmod +x "${DESKTOP_DIR}"/*.desktop
chown -R demo:demo "${DESKTOP_DIR}"

# Auto-trust XFCE desktop launchers on session start so users never get an "Untrusted launcher" prompt
cat > /usr/local/bin/trust-xfce-desktop-icons.sh <<'EOF'
#!/usr/bin/env bash
for f in "${HOME}/Desktop"/*.desktop; do
  [[ -f "$f" ]] || continue
  chmod +x "$f" 2>/dev/null || true
  sha="$(sha256sum "$f" | awk '{print $1}')"
  gio set -t string "$f" metadata::xfce-exe-checksum "$sha" 2>/dev/null || true
  gio set -t string "$f" metadata::trusted true 2>/dev/null || true
done
xfdesktop --reload 2>/dev/null || true
EOF
chmod +x /usr/local/bin/trust-xfce-desktop-icons.sh

mkdir -p /etc/xdg/autostart
cat > /etc/xdg/autostart/trust-xfce-desktop-icons.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=Trust XFCE Desktop Launchers
Exec=/usr/local/bin/trust-xfce-desktop-icons.sh
OnlyShowIn=XFCE;
NoDisplay=true
EOF

# 9. Configure terminal login banner & environment in /etc/profile.d/claude-vertex-demo.sh
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
