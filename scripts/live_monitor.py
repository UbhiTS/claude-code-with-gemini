#!/usr/bin/env python3
"""Live-updating top-right tmux pane telemetry & cost monitor.

Streams `logs/telemetry.jsonl` and `logs/active_context.json` in real time,
displaying the active agent stage, Vertex AI model routing, per-turn token
throughput, cumulative cost ($), and live counterfactual savings vs 100% Opus 5.5.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.what_if_engine import build_what_if_analysis, load_telemetry, select_run_rows

ACTIVE_CONTEXT_FILE = REPO_ROOT / "logs" / "active_context.json"


def read_active_ctx() -> Dict[str, Any]:
    if ACTIVE_CONTEXT_FILE.exists():
        try:
            return json.loads(ACTIVE_CONTEXT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"run_id": "waiting", "task_size": "small", "agent_role": "Idle", "configured_model": ""}


def render_frame(all_rows: List[Dict[str, Any]]) -> str:
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    MAGENTA = "\033[95m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    ctx = read_active_ctx()
    run_id, task_size, run_rows = select_run_rows(all_rows)
    lines: List[str] = []
    lines.append(f"{BOLD}{CYAN}┌──────────────────────────────────────────────────────────────────────────────┐{RESET}")
    lines.append(f"{BOLD}{CYAN}│  VERTEX AI LITELLM HYBRID ROUTER — REAL-TIME TELEMETRY & COST MONITOR       │{RESET}")
    lines.append(f"{BOLD}{CYAN}└──────────────────────────────────────────────────────────────────────────────┘{RESET}")
    lines.append(
        f"  Active Stage : {BOLD}{YELLOW}{ctx.get('agent_role', 'Idle')}{RESET} "
        f"({ctx.get('configured_model', 'ready')})   |   Run: {BOLD}{run_id}{RESET}"
    )
    lines.append("")

    if not run_rows:
        lines.append(f"  {DIM}Waiting for Claude Code requests on http://127.0.0.1:4000/v1/messages ...{RESET}")
        return "\n".join(lines)

    report = build_what_if_analysis(run_rows=run_rows, all_rows=all_rows, run_id=run_id, task_size=task_size)
    lines.append(f"  {BOLD}{'Stage':<12} {'Model':<18} {'Turns':>5} {'Tools':>5} {'Tokens':>9} {'Latency':>8} {'Cost ($)':>10}{RESET}")
    lines.append("  " + "─" * 73)
    for role in ("Planner", "Implementer", "Reviewer"):
        st = report["stages"].get(role)
        if not st:
            continue
        color = MAGENTA if st["publisher"] == "anthropic" else GREEN
        lines.append(
            f"  {color}{role:<12} {st['model_label']:<18}{RESET} {st['turns']:>5} {st['tool_calls']:>5} "
            f"{st['total_tokens']:>9,} {st['latency_s']:>7.1f}s ${st['cost_usd']:>9.5f}"
        )
    lines.append("  " + "─" * 73)
    tot = report["totals"]
    sc_actual = report["scenarios"][0]
    sc_opus = report["scenarios"][1]
    lines.append(
        f"  {BOLD}{'TOTAL':<12} {'Hybrid Routing':<18} {tot['turns']:>5} {tot['tool_calls']:>5} "
        f"{tot['total_tokens']:>9,} {tot['latency_s']:>7.1f}s ${tot['cost_usd']:>9.5f}{RESET}"
    )
    lines.append("")
    lines.append(
        f"  {BOLD}{GREEN}Live Savings vs 100% Opus 5.5:{RESET} "
        f"${sc_actual['total_cost_usd']:.5f} vs ${sc_opus['total_cost_usd']:.5f} "
        f"({BOLD}{GREEN}-{sc_actual['savings_vs_opus_pct']:.1f}% Cost{RESET} | "
        f"{BOLD}{GREEN}-{sc_actual['time_saved_vs_opus_s']:.1f}s Time{RESET})"
    )
    lines.append("")
    lines.append(f"  {BOLD}Recent API Turns:{RESET}")
    for r in run_rows[-5:]:
        ts = str(r.get("timestamp", ""))[11:19]
        role = str(r.get("agent_role", ""))[:11]
        m_id = str(r.get("model", ""))[:16]
        in_t = int(r.get("prompt_tokens", 0))
        out_t = int(r.get("completion_tokens", 0))
        lat_s = float(r.get("latency_ms", 0.0)) / 1000.0
        cost = float(r.get("cost_usd", 0.0))
        lines.append(
            f"    [{ts}] {role:<11} -> {m_id:<16} | in={in_t:>5,} out={out_t:>4,} | {lat_s:>4.1f}s | ${cost:.5f}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Live telemetry monitor for tmux pane")
    parser.add_argument("--once", action="store_true", help="Render one frame and exit")
    parser.add_argument("--interval", type=float, default=1.0, help="Refresh interval in seconds")
    args = parser.parse_args()

    if args.once:
        print(render_frame(load_telemetry()))
        return 0

    try:
        while True:
            frame = render_frame(load_telemetry())
            sys.stdout.write("\033[2J\033[H" + frame + "\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
