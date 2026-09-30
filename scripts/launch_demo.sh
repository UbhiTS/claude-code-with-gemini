#!/usr/bin/env bash
# Shared launcher for small.sh, medium.sh, and large.sh.
# Opens TWO separate desktop windows side-by-side (NO tmux splits):
#   - Window 1 (Left)  : 100% Pure Interactive Claude Code (`claude`)
#   - Window 2 (Right) : Standalone Live Telemetry & What-If Cost/Speed Comparison (`scripts/live_monitor.py`)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
export PATH="${REPO_ROOT}/.venv/bin:/usr/local/google/home/ubhi/bin:${HOME}/bin:${HOME}/.local/node/bin:/usr/local/bin:${PATH}"

if [[ -f "${REPO_ROOT}/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${REPO_ROOT}/.venv/bin/activate"
fi

TASK_SIZE="${1:-small}"
shift || true

FROM_DESKTOP=0
SPAWN_WHATIF_WINDOW=1
KEEP_OPEN=1
PIPELINE_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from-desktop)
      FROM_DESKTOP=1
      shift
      ;;
    --no-window|--no-tmux|--direct)
      SPAWN_WHATIF_WINDOW=0
      KEEP_OPEN=0
      shift
      ;;
    --auto-exit)
      KEEP_OPEN=0
      shift
      ;;
    *)
      PIPELINE_ARGS+=("$1")
      shift
      ;;
  esac
done

# Clean up any legacy tmux session if one was left running previously
tmux kill-session -t "claude-vertex-small" 2>/dev/null || true
tmux kill-session -t "claude-vertex-medium" 2>/dev/null || true
tmux kill-session -t "claude-vertex-large" 2>/dev/null || true

# Auto-detect active XFCE/Xrdp DISPLAY (:10.0) if running on the demo VM
if [[ -z "${DISPLAY:-}" ]] && [[ -S /tmp/.X11-unix/X10 ]]; then
  export DISPLAY=":10.0"
fi

if [[ "${SPAWN_WHATIF_WINDOW}" -eq 1 ]] && [[ -n "${DISPLAY:-}" ]] && command -v xfce4-terminal >/dev/null 2>&1; then
  # Close any previous What-If monitor window so we don't stack duplicates
  pkill -f "scripts/live_monitor.py" 2>/dev/null || true

  # Reset active_context.json immediately so Window 2 shows the new run starting
  mkdir -p "${REPO_ROOT}/logs"
  cat > "${REPO_ROOT}/logs/active_context.json" <<EOF
{
  "run_id": "${TASK_SIZE}-starting",
  "task_size": "${TASK_SIZE}",
  "agent_role": "Stage 1: Planner",
  "stage_index": 1,
  "configured_model": "claude-opus-5-5",
  "workspace": "",
  "status": "running",
  "pytest_summary": ""
}
EOF

  # Launch Window 2 (Right Desktop Window): Standalone Live Telemetry & What-If Comparison
  nohup xfce4-terminal \
    --disable-server \
    --title="Vertex AI LiteLLM — Live Telemetry & What-If Cost/Speed Comparison" \
    --geometry=104x44+880+25 \
    -e "bash -c 'cd \"${REPO_ROOT}\" && source .venv/bin/activate 2>/dev/null || true; exec python3 scripts/live_monitor.py'" \
    >/dev/null 2>&1 &

  if [[ "${FROM_DESKTOP}" -eq 1 ]]; then
    # Launch Window 1 (Left Desktop Window): 100% Pure Interactive Claude Code
    exec xfce4-terminal \
      --disable-server \
      --title="Claude Code" \
      --geometry=96x44+15+25 \
      -e "bash -c 'cd \"${REPO_ROOT}\" && source .venv/bin/activate 2>/dev/null || true; exec python3 scripts/run_pipeline.py --task \"${TASK_SIZE}\" --keep-open ${PIPELINE_ARGS[*]:-}'"
  else
    # Position and title the current terminal window as Window 1 (Left: Claude Code)
    printf '\033]0;Claude Code\007\033[3;15;25t\033[8;44;96t' || true
  fi
fi

EXTRA_FLAGS=()
if [[ "${KEEP_OPEN}" -eq 1 ]] && [[ -t 1 ]]; then
  EXTRA_FLAGS+=("--keep-open")
fi

exec python3 "${REPO_ROOT}/scripts/run_pipeline.py" --task "${TASK_SIZE}" "${EXTRA_FLAGS[@]}" "${PIPELINE_ARGS[@]}"
