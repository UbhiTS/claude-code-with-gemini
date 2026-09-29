#!/usr/bin/env bash
# Run the SMALL 3-stage Claude Code + Vertex AI benchmark (Token-Bucket Rate Limiter & CLI)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "${REPO_ROOT}/scripts/launch_demo.sh" small "$@"
