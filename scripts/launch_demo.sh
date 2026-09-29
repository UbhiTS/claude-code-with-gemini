#!/usr/bin/env bash
# Shared launcher for small.sh, medium.sh, and large.sh.
# Launches a 3-pane tmux split dashboard when interactive, or runs directly in
# the foreground when --no-tmux is passed (or when stdout is non-interactive).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PATH="/usr/local/google/home/ubhi/bin:${HOME}/bin:${HOME}/.local/node/bin:${PATH}"

TASK_SIZE="${1:-small}"
shift || true

USE_TMUX=1
PIPELINE_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-tmux|--direct)
      USE_TMUX=0
      shift
      ;;
    --tmux)
      USE_TMUX=1
      shift
      ;;
    *)
      PIPELINE_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ ! -t 1 ]] || [[ "${NO_TMUX:-0}" == "1" ]] || ! command -v tmux >/dev/null 2>&1; then
  USE_TMUX=0
fi

if [[ "${USE_TMUX}" -eq 1 ]] && [[ -z "${TMUX:-}" ]]; then
  SESSION_NAME="claude-vertex-${TASK_SIZE}"
  tmux kill-session -t "${SESSION_NAME}" 2>/dev/null || true

  CMD_LEFT="cd '${REPO_ROOT}' && python3 scripts/run_pipeline.py --task '${TASK_SIZE}' ${PIPELINE_ARGS[*]:-}; echo ''; echo 'Press Enter to close tmux session...'; read -r"
  CMD_TOP_RIGHT="cd '${REPO_ROOT}' && python3 scripts/live_monitor.py"
  CMD_BOT_RIGHT="cd '${REPO_ROOT}' && python3 scripts/workspace_watch.py"

  tmux new-session -d -s "${SESSION_NAME}" -x 180 -y 50 "${CMD_LEFT}"
  tmux split-window -h -t "${SESSION_NAME}:0.0" -p 42 "${CMD_TOP_RIGHT}"
  tmux split-window -v -t "${SESSION_NAME}:0.1" -p 45 "${CMD_BOT_RIGHT}"
  tmux select-pane -t "${SESSION_NAME}:0.0"
  exec tmux attach-session -t "${SESSION_NAME}"
else
  exec python3 "${REPO_ROOT}/scripts/run_pipeline.py" --task "${TASK_SIZE}" "${PIPELINE_ARGS[@]}"
fi
