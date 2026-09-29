#!/usr/bin/env bash
# Run the LARGE 3-stage Claude Code + Vertex AI benchmark (Cloud FinOps Anomaly Platform)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${REPO_ROOT}/scripts/launch_demo.sh" large "$@"
