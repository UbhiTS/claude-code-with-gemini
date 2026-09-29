#!/usr/bin/env bash
# View or update the 3-agent model assignments in config/models.env
# Usage:
#   ./configure-models.sh                                  # Display current configuration & catalog
#   ./configure-models.sh --preset default                 # Opus 5.5 + Gemini 3.8 Flash + Sonnet 5
#   ./configure-models.sh --preset all-opus                # 100% Claude Opus 5.5
#   ./configure-models.sh --preset all-sonnet              # 100% Claude Sonnet 5
#   ./configure-models.sh --preset budget                  # 100% Gemini 3.8 Flash
#   ./configure-models.sh --planner claude-opus-5-5 --implementer gemini-3.7-flash --reviewer claude-sonnet-5
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${REPO_ROOT}/config/models.env"

if [[ -f "${ENV_FILE}" ]]; then
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
fi

PLANNER="${PLANNER_MODEL:-claude-opus-5-5}"
IMPLEMENTER="${IMPLEMENTER_MODEL:-gemini-3.8-flash}"
REVIEWER="${REVIEWER_MODEL:-claude-sonnet-5}"
P_EFFORT="${PLANNER_EFFORT:-high}"
I_EFFORT="${IMPLEMENTER_EFFORT:-medium}"
R_EFFORT="${REVIEWER_EFFORT:-medium}"

UPDATED=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --preset)
      PRESET="${2:-default}"
      case "${PRESET}" in
        default|hybrid)
          PLANNER="claude-opus-5-5"
          IMPLEMENTER="gemini-3.8-flash"
          REVIEWER="claude-sonnet-5"
          ;;
        all-opus)
          PLANNER="claude-opus-5-5"
          IMPLEMENTER="claude-opus-5-5"
          REVIEWER="claude-opus-5-5"
          ;;
        all-sonnet)
          PLANNER="claude-sonnet-5"
          IMPLEMENTER="claude-sonnet-5"
          REVIEWER="claude-sonnet-5"
          ;;
        budget|all-flash)
          PLANNER="gemini-3.8-flash"
          IMPLEMENTER="gemini-3.8-flash"
          REVIEWER="gemini-3.8-flash"
          ;;
        *)
          echo "Unknown preset: ${PRESET} (expected: default, all-opus, all-sonnet, budget)" >&2
          exit 1
          ;;
      esac
      UPDATED=1
      shift 2
      ;;
    --planner)
      PLANNER="$2"
      UPDATED=1
      shift 2
      ;;
    --implementer)
      IMPLEMENTER="$2"
      UPDATED=1
      shift 2
      ;;
    --reviewer)
      REVIEWER="$2"
      UPDATED=1
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

if [[ "${UPDATED}" -eq 1 ]]; then
  cat > "${ENV_FILE}" <<EOF
# ==============================================================================
# Claude Code + Vertex AI Hybrid Multi-Agent Configuration
# ==============================================================================
PLANNER_MODEL=${PLANNER}
IMPLEMENTER_MODEL=${IMPLEMENTER}
REVIEWER_MODEL=${REVIEWER}

PLANNER_EFFORT=${P_EFFORT}
IMPLEMENTER_EFFORT=${I_EFFORT}
REVIEWER_EFFORT=${R_EFFORT}

GATEWAY_HOST=${GATEWAY_HOST:-127.0.0.1}
GATEWAY_PORT=${GATEWAY_PORT:-4000}
VERTEX_PROJECT_ID=${VERTEX_PROJECT_ID:-llm-compare-ubhits}
VERTEX_REGION_CLAUDE=${VERTEX_REGION_CLAUDE:-us-east5}
VERTEX_REGION_GEMINI=${VERTEX_REGION_GEMINI:-global}
EOF
  echo "Updated ${ENV_FILE}!"
fi

echo "======================================================================"
echo "  Active 3-Agent Model Configuration (config/models.env)"
echo "======================================================================"
echo "  Stage 1 — Planner     : ${PLANNER} (effort=${P_EFFORT})"
echo "  Stage 2 — Implementer : ${IMPLEMENTER} (effort=${I_EFFORT})"
echo "  Stage 3 — Reviewer    : ${REVIEWER} (effort=${R_EFFORT})"
echo "======================================================================"
echo "  Supported Vertex AI Models:"
echo "    • claude-opus-5-5       (\$5.00 in / \$25.00 out per 1M tokens)"
echo "    • claude-sonnet-5       (\$3.00 in / \$15.00 out per 1M tokens)"
echo "    • gemini-3.8-flash      (\$0.75 in / \$3.75  out per 1M tokens)"
echo "    • gemini-3.7-flash      (\$0.50 in / \$3.00  out per 1M tokens)"
echo "    • gemini-3.5-flash      (\$0.30 in / \$2.50  out per 1M tokens)"
echo "    • gemini-3.5-flash-lite (\$0.10 in / \$0.40  out per 1M tokens)"
echo "    • gemini-3.1-pro        (\$2.00 in / \$12.00 out per 1M tokens)"
echo "======================================================================"
