#!/usr/bin/env bash
# Run interactive or CLI counterfactual What-If simulations across any Planner/Implementer/Reviewer models
# Usage:
#   ./what-if.sh --planner claude-opus-5-5 --implementer claude-opus-5-5 --reviewer claude-opus-5-5
#   ./what-if.sh --planner claude-sonnet-5 --implementer gemini-3.8-flash --reviewer claude-sonnet-5 --devs 250
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${REPO_ROOT}/src/what_if_engine.py" "$@"
