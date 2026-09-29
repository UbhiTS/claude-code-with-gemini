#!/usr/bin/env bash
# Run the MEDIUM 3-stage Claude Code + Vertex AI benchmark (Payment & Order Fulfillment Microservice)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${REPO_ROOT}/scripts/launch_demo.sh" medium "$@"
