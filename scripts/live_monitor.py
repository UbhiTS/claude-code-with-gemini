#!/usr/bin/env python3
"""Standalone Window 2: Live Vertex AI LiteLLM Telemetry & What-If Cost/Speed Analyzer.

Runs in its own dedicated desktop window alongside the pure Claude Code window
(Window 1). Streams `logs/telemetry.jsonl` and `logs/active_context.json` in
real time, updating the 3-stage telemetry table, Implementer spotlight,
5-scenario Counterfactual What-If Matrix, and Enterprise Annual ROI projection
after every Vertex AI API call.
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
    return {
        "run_id": "waiting",
        "task_size": "small",
        "agent_role": "Idle",
        "configured_model": "",
        "workspace": "",
        "status": "idle",
        "pytest_summary": "",
    }


def _inspect_workspace_artifacts(ws_path_str: str) -> str:
    if not ws_path_str:
        return "Waiting for workspace..."
    ws = Path(ws_path_str)
    if not ws.exists():
        return "Initializing workspace..."
    badges: List[str] = []
    plan_file = ws / "PLAN.md"
    rev_file = ws / "REVIEW.md"
    if plan_file.exists() and plan_file.stat().st_size > 0:
        badges.append(f"\033[92m✓ PLAN.md ({plan_file.stat().st_size:,} B)\033[0m")
    else:
        badges.append("\033[2m○ PLAN.md\033[0m")

    py_files = [
        p for p in ws.rglob("*.py")
        if not any(part.startswith(".") or part == "__pycache__" or part == "tests" for part in p.parts)
    ]
    badges.append(f"\033[96m{len(py_files)} src .py files\033[0m")

    if rev_file.exists() and rev_file.stat().st_size > 0:
        badges.append(f"\033[92m✓ REVIEW.md ({rev_file.stat().st_size:,} B)\033[0m")
    else:
        badges.append("\033[2m○ REVIEW.md\033[0m")
    return "  |  ".join(badges)


def render_frame(all_rows: List[Dict[str, Any]]) -> str:
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    MAGENTA = "\033[95m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    ctx = read_active_ctx()
    ctx_run_id = str(ctx.get("run_id") or "")
    ctx_task_size = str(ctx.get("task_size") or "small")
    if ctx_run_id and ctx_run_id != "waiting":
        run_id, task_size, run_rows = select_run_rows(all_rows, run_id=ctx_run_id, task_size=ctx_task_size)
        if not run_rows:
            run_id = ctx_run_id
            task_size = ctx_task_size
    else:
        run_id, task_size, run_rows = select_run_rows(all_rows)

    status = str(ctx.get("status") or "running")
    role_str = str(ctx.get("agent_role") or "Idle")
    cfg_model = str(ctx.get("configured_model") or "ready")
    pytest_summary = str(ctx.get("pytest_summary") or "")
    ws_str = str(ctx.get("workspace") or "")

    lines: List[str] = []
    lines.append(f"{BOLD}{CYAN}=================================================================================================={RESET}")
    lines.append(f"{BOLD}{CYAN}  CLAUDE CODE + VERTEX AI LITELLM GATEWAY — LIVE TELEMETRY & WHAT-IF ECONOMICS MONITOR{RESET}")
    lines.append(f"{BOLD}{CYAN}=================================================================================================={RESET}")

    status_badge = f"{BOLD}{GREEN}COMPLETED ✓{RESET}" if status == "completed" else f"{BOLD}{YELLOW}LIVE RUNNING ●{RESET}"
    lines.append(
        f"  Status : {status_badge}  {BOLD}{role_str}{RESET} ({cfg_model})"
        + (f"  |  Pytest: {BOLD}{GREEN}{pytest_summary}{RESET}" if pytest_summary else "")
    )
    lines.append(
        f"  Run ID : {BOLD}{run_id}{RESET} ({task_size.upper()})  |  Artifacts: {_inspect_workspace_artifacts(ws_str)}"
    )
    lines.append("  " + "─" * 94)

    if not run_rows:
        lines.append("")
        lines.append(f"  {YELLOW}▶ Waiting for first Vertex AI API turn from Claude Code window (`http://127.0.0.1:4000`)...{RESET}")
        lines.append(f"  {DIM}  Stage 1 (@planner)     -> Claude Opus 5.5  ($5.00 in / $25.00 out per 1M){RESET}")
        lines.append(f"  {DIM}  Stage 2 (@implementer) -> Gemini 3.8 Flash ($0.75 in / $3.75 out per 1M){RESET}")
        lines.append(f"  {DIM}  Stage 3 (@reviewer)    -> Claude Sonnet 5  ($3.00 in / $15.00 out per 1M){RESET}")
        return "\n".join(lines)

    report = build_what_if_analysis(run_rows=run_rows, all_rows=all_rows, run_id=run_id, task_size=task_size)
    tot = report["totals"]

    # 1. 3-Stage Breakdown
    lines.append(f"{BOLD}1. ACTUAL 3-STAGE PIPELINE EXECUTION (LIVE VERTEX AI TELEMETRY){RESET}")
    lines.append(
        f"  {BOLD}{'Stage':<12} {'Model':<18} {'Turns':>5} {'Tools':>5} {'In Tok':>9} {'Out Tok':>8} {'Tok %':>6} {'Latency':>8} {'Cost ($)':>10}{RESET}"
    )
    lines.append("  " + "─" * 90)
    for role in ("Planner", "Implementer", "Reviewer"):
        st = report["stages"].get(role)
        if not st or st["turns"] == 0:
            lines.append(f"  {DIM}{role:<12} {'(pending...)':<18} {'-':>5} {'-':>5} {'-':>9} {'-':>8} {'-':>6} {'-':>8} {'-':>10}{RESET}")
            continue
        color = MAGENTA if st["publisher"] == "anthropic" else GREEN
        lines.append(
            f"  {color}{role:<12} {st['model_label']:<18}{RESET} {st['turns']:>5} {st['tool_calls']:>5} "
            f"{st['prompt_tokens']:>9,} {st['completion_tokens']:>8,} {st['token_share_pct']:>5.1f}% "
            f"{st['latency_s']:>7.2f}s ${st['cost_usd']:>9.5f}"
        )
    lines.append("  " + "─" * 90)
    lines.append(
        f"  {BOLD}{'TOTAL':<12} {'Hybrid 3-Agent':<18} {tot['turns']:>5} {tot['tool_calls']:>5} "
        f"{tot['prompt_tokens']:>9,} {tot['completion_tokens']:>8,} {'100.0%':>6} "
        f"{tot['latency_s']:>7.2f}s ${tot['cost_usd']:>9.5f}{RESET}"
    )
    lines.append("")

    # 2. Implementer Spotlight
    impl_c = report.get("implementer_comparison") or {}
    if impl_c and impl_c.get("prompt_tokens", 0) > 0:
        lines.append(f"{BOLD}2. IMPLEMENTER STAGE SPOTLIGHT (WHY GEMINI 3.8 FLASH WINS THE CODING LOOP){RESET}")
        lines.append(
            f"  Implementer processed {BOLD}{impl_c['prompt_tokens']:,}{RESET} in + {BOLD}{impl_c['completion_tokens']:,}{RESET} out tokens:"
        )
        lines.append(
            f"    • {GREEN}{BOLD}Gemini 3.8 Flash ($0.75 / $3.75 per 1M):  ${impl_c['flash_cost_usd']:.5f}{RESET}  "
            f"({BOLD}{GREEN}-{impl_c['savings_vs_opus_pct']:.1f}% vs Opus 5.5, -{impl_c['savings_vs_sonnet_pct']:.1f}% vs Sonnet 5{RESET})"
        )
        lines.append(
            f"    • Claude Sonnet 5  ($3.00 / $15.00 per 1M): ${impl_c['sonnet_cost_usd']:.5f}  (4.0x more expensive than Gemini 3.8 Flash)"
        )
        lines.append(
            f"    • Claude Opus 5.5  ($5.00 / $25.00 per 1M): ${impl_c['opus_cost_usd']:.5f}  (6.7x more expensive than Gemini 3.8 Flash)"
        )
        lines.append("")

    # 3. Counterfactual What-If Matrix
    lines.append(f"{BOLD}3. COUNTERFACTUAL WHAT-IF MATRIX (SAME TOKENS & TOOL TURNS ACROSS ARCHITECTURES){RESET}")
    lines.append(
        f"  {BOLD}{'Architecture Scenario':<56} {'Run Cost':>10} {'vs All-Opus':>11} {'Est Time':>9} {'Time Saved':>10}{RESET}"
    )
    lines.append("  " + "─" * 94)
    for idx, sc in enumerate(report["scenarios"]):
        name = sc["name"][:54]
        vs_opus = "baseline" if abs(sc["savings_vs_opus_pct"]) < 0.05 and "100% Claude Opus" in sc["name"] else f"-{sc['savings_vs_opus_pct']:.1f}%"
        t_saved = "baseline" if abs(sc["time_saved_vs_opus_s"]) < 0.05 and "100% Claude Opus" in sc["name"] else f"-{sc['time_saved_vs_opus_s']:.1f}s"
        prefix = f"{GREEN}{BOLD}* " if idx == 0 else "  "
        suffix = RESET if idx == 0 else ""
        lines.append(
            f"{prefix}{name:<56} ${sc['total_cost_usd']:>9.5f} {vs_opus:>11} {sc['total_latency_s']:>8.1f}s {t_saved:>10}{suffix}"
        )
    lines.append("")

    # 4. Enterprise Annual Projection
    lines.append(f"{BOLD}4. ENTERPRISE ANNUAL PROJECTION (100 Devs × 15 Tasks/Day × 250 Days = 375,000 Runs/Yr){RESET}")
    lines.append(
        f"  {BOLD}{'Architecture Scenario':<56} {'Annual Spend':>14} {'Annual Savings':>15} {'Dev Hrs Saved':>13}{RESET}"
    )
    lines.append("  " + "─" * 94)
    for idx, sc in enumerate(report["scenarios"]):
        name = sc["name"][:54]
        prefix = f"{GREEN}{BOLD}* " if idx == 0 else "  "
        suffix = RESET if idx == 0 else ""
        lines.append(
            f"{prefix}{name:<56} ${sc['annual_cost_usd']:>13,.2f} ${sc['annual_savings_vs_opus_usd']:>14,.2f} {sc['annual_hours_saved_vs_opus']:>9,.1f} hrs{suffix}"
        )
    lines.append("")

    # 5. Live API Turn Stream
    lines.append(f"{BOLD}5. LIVE VERTEX AI GATEWAY TURN STREAM (LAST 6 TURNS){RESET}")
    for r in run_rows[-6:]:
        ts = str(r.get("timestamp", ""))[11:19]
        role = str(r.get("agent_role", ""))[:11]
        m_id = str(r.get("model", ""))[:18]
        in_t = int(r.get("prompt_tokens", 0))
        out_t = int(r.get("completion_tokens", 0))
        tools_n = int(r.get("tool_calls", 0))
        lat_s = float(r.get("latency_ms", 0.0)) / 1000.0
        cost = float(r.get("cost_usd", 0.0))
        c_col = MAGENTA if r.get("publisher") == "anthropic" else GREEN
        lines.append(
            f"  [{ts}] {c_col}{role:<11} -> {m_id:<18}{RESET} | in={in_t:>6,} out={out_t:>4,} tools={tools_n} | {lat_s:>5.2f}s | ${cost:.5f}"
        )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Standalone Live Telemetry & What-If Cost/Speed Analyzer Window")
    parser.add_argument("--once", action="store_true", help="Render one frame and exit")
    parser.add_argument("--interval", type=float, default=0.8, help="Refresh interval in seconds")
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
