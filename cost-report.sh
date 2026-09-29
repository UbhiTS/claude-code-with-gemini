#!/usr/bin/env bash
# Render the executive cost, token, speed, and counterfactual What-If report
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${REPO_ROOT}/src/what_if_engine.py" "$@"
