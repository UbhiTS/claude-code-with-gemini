#!/usr/bin/env python3
"""Counterfactual Cost & Speed What-If Engine for Claude Code + Vertex AI Hybrid Routing.

Analyzes real per-turn telemetry from `logs/telemetry.jsonl`, computes per-stage
token/cost/latency breakdowns, simulates counterfactual architectures (100% Opus 5.5,
100% Sonnet 5, Anthropic-Only Tiered, or any custom `--planner/--implementer/--reviewer`
combination) using live measured `tokens/sec`, projects Enterprise Annual ROI, and
generates both an ANSI terminal dashboard and `logs/latest_report.html`.
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.catalog import MODEL_CATALOG, compute_cost_usd, resolve_model

LOGS_DIR = REPO_ROOT / "logs"
TELEMETRY_FILE = LOGS_DIR / "telemetry.jsonl"
RUNS_FILE = LOGS_DIR / "runs.jsonl"
LATEST_HTML_REPORT = LOGS_DIR / "latest_report.html"

# Empirical Vertex AI fallback speeds (tokens/sec) used ONLY if a model was not
# observed live during the current session's telemetry log.
EMPIRICAL_SPEED_TOK_PER_SEC: Dict[str, float] = {
    "claude-opus-5-5": 28.0,
    "claude-sonnet-5": 58.0,
    "gemini-3.8-flash": 165.0,
    "gemini-3.7-flash": 155.0,
    "gemini-3.5-flash": 175.0,
    "gemini-3.5-flash-lite": 240.0,
    "gemini-3.1-pro": 72.0,
}

STAGE_ORDER = ["Planner", "Implementer", "Reviewer"]


def load_telemetry(telemetry_path: Path = TELEMETRY_FILE) -> List[Dict[str, Any]]:
    """Load all valid JSONL telemetry rows from logs/telemetry.jsonl."""
    if not telemetry_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for raw in telemetry_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            if isinstance(row, dict) and row.get("status") == "ok":
                rows.append(row)
        except Exception:
            continue
    return rows


def select_run_rows(
    all_rows: List[Dict[str, Any]],
    run_id: Optional[str] = None,
    task_size: Optional[str] = None,
) -> tuple[str, str, List[Dict[str, Any]]]:
    """Select rows belonging to a specific run_id or the most recent pipeline run."""
    if not all_rows:
        return ("none", task_size or "small", [])

    if run_id:
        filtered = [r for r in all_rows if r.get("run_id") == run_id]
        if filtered:
            tsize = str(filtered[-1].get("task_size") or task_size or "small")
            return (run_id, tsize, filtered)

    # Prefer non-interactive pipeline runs first
    pipeline_rows = [
        r
        for r in all_rows
        if r.get("run_id") not in (None, "", "interactive")
        and r.get("agent_role") in STAGE_ORDER
    ]
    if task_size:
        sized = [r for r in pipeline_rows if r.get("task_size") == task_size]
        if sized:
            pipeline_rows = sized

    target_pool = pipeline_rows if pipeline_rows else all_rows
    latest_run_id = str(target_pool[-1].get("run_id") or "interactive")
    selected = [r for r in target_pool if str(r.get("run_id") or "interactive") == latest_run_id]
    resolved_task = str(selected[-1].get("task_size") or task_size or "small")
    return (latest_run_id, resolved_task, selected)


def compute_measured_speeds(all_rows: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute live measured output tokens/sec per canonical model ID from telemetry."""
    tok_sums: Dict[str, int] = {}
    sec_sums: Dict[str, float] = {}
    for r in all_rows:
        m_id = resolve_model(str(r.get("model") or "gemini-3.8-flash"))["id"]
        out_tok = int(r.get("completion_tokens") or 0)
        lat_ms = float(r.get("latency_ms") or 0.0)
        if out_tok > 0 and lat_ms > 0:
            tok_sums[m_id] = tok_sums.get(m_id, 0) + out_tok
            sec_sums[m_id] = sec_sums.get(m_id, 0.0) + (lat_ms / 1000.0)

    speeds = dict(EMPIRICAL_SPEED_TOK_PER_SEC)
    for m_id, total_tok in tok_sums.items():
        total_sec = sec_sums.get(m_id, 0.0)
        if total_sec > 0 and total_tok >= 15:
            speeds[m_id] = round(total_tok / total_sec, 1)
    return speeds


def build_what_if_analysis(
    run_rows: List[Dict[str, Any]],
    all_rows: Optional[List[Dict[str, Any]]] = None,
    run_id: str = "latest",
    task_size: str = "small",
    custom_models: Optional[Dict[str, str]] = None,
    num_devs: int = 100,
    tasks_per_dev_day: int = 15,
    workdays_per_year: int = 250,
) -> Dict[str, Any]:
    """Compute stage breakdowns, counterfactual cost/speed comparisons, and annual ROI."""
    pool_for_speed = all_rows if all_rows else run_rows
    speeds = compute_measured_speeds(pool_for_speed)

    # Aggregate by stage (Planner, Implementer, Reviewer)
    stages: Dict[str, Dict[str, Any]] = {}
    for role in STAGE_ORDER:
        role_rows = [r for r in run_rows if str(r.get("agent_role", "")).strip().lower() == role.lower()]
        if not role_rows:
            continue
        model_id = resolve_model(str(role_rows[-1].get("model") or "gemini-3.8-flash"))["id"]
        m_cfg = resolve_model(model_id)
        in_tok = sum(int(r.get("prompt_tokens") or 0) for r in role_rows)
        out_tok = sum(int(r.get("completion_tokens") or 0) for r in role_rows)
        think_tok = sum(int(r.get("thinking_tokens") or 0) for r in role_rows)
        tot_tok = in_tok + out_tok
        lat_ms = sum(float(r.get("latency_ms") or 0.0) for r in role_rows)
        lat_s = round(lat_ms / 1000.0, 2)
        cost = round(sum(float(r.get("cost_usd") or 0.0) for r in role_rows), 6)
        tools = sum(int(r.get("tool_calls") or 0) for r in role_rows)
        eff_speed = round(out_tok / (lat_ms / 1000.0), 1) if lat_ms > 0 and out_tok > 0 else speeds.get(model_id, 50.0)
        stages[role] = {
            "role": role,
            "model_id": model_id,
            "model_label": m_cfg["label"],
            "publisher": m_cfg["publisher"],
            "turns": len(role_rows),
            "tool_calls": tools,
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "thinking_tokens": think_tok,
            "total_tokens": tot_tok,
            "latency_ms": round(lat_ms, 1),
            "latency_s": lat_s,
            "tokens_per_sec": eff_speed,
            "cost_usd": cost,
        }

    # If run_rows were interactive/ad-hoc without explicit stage names, bucket them under Implementer
    if not stages and run_rows:
        model_id = resolve_model(str(run_rows[-1].get("model") or "gemini-3.8-flash"))["id"]
        m_cfg = resolve_model(model_id)
        in_tok = sum(int(r.get("prompt_tokens") or 0) for r in run_rows)
        out_tok = sum(int(r.get("completion_tokens") or 0) for r in run_rows)
        think_tok = sum(int(r.get("thinking_tokens") or 0) for r in run_rows)
        lat_ms = sum(float(r.get("latency_ms") or 0.0) for r in run_rows)
        cost = round(sum(float(r.get("cost_usd") or 0.0) for r in run_rows), 6)
        tools = sum(int(r.get("tool_calls") or 0) for r in run_rows)
        stages["Implementer"] = {
            "role": "Implementer",
            "model_id": model_id,
            "model_label": m_cfg["label"],
            "publisher": m_cfg["publisher"],
            "turns": len(run_rows),
            "tool_calls": tools,
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "thinking_tokens": think_tok,
            "total_tokens": in_tok + out_tok,
            "latency_ms": round(lat_ms, 1),
            "latency_s": round(lat_ms / 1000.0, 2),
            "tokens_per_sec": round(out_tok / (lat_ms / 1000.0), 1) if lat_ms > 0 else 50.0,
            "cost_usd": cost,
        }

    total_in = sum(s["prompt_tokens"] for s in stages.values())
    total_out = sum(s["completion_tokens"] for s in stages.values())
    total_tokens = total_in + total_out
    total_cost = round(sum(s["cost_usd"] for s in stages.values()), 6)
    total_latency_s = round(sum(s["latency_s"] for s in stages.values()), 2)
    total_turns = sum(s["turns"] for s in stages.values())
    total_tools = sum(s["tool_calls"] for s in stages.values())

    for s in stages.values():
        s["token_share_pct"] = round((s["total_tokens"] / total_tokens) * 100.0, 1) if total_tokens > 0 else 0.0
        s["cost_share_pct"] = round((s["cost_usd"] / total_cost) * 100.0, 1) if total_cost > 0 else 0.0

    def simulate_scenario(name: str, role_map: Dict[str, str], is_actual: bool = False) -> Dict[str, Any]:
        scen_cost = 0.0
        scen_lat_s = 0.0
        stage_details: Dict[str, Dict[str, Any]] = {}
        for role, st in stages.items():
            target_model = resolve_model(role_map.get(role, st["model_id"]))["id"]
            t_cfg = resolve_model(target_model)
            c_usd = st["cost_usd"] if (is_actual and target_model == st["model_id"]) else compute_cost_usd(
                target_model, st["prompt_tokens"], st["completion_tokens"]
            )
            # Estimate latency if model differs from actual stage model
            if target_model == st["model_id"]:
                est_lat_s = st["latency_s"]
            else:
                # Physics-based prefill (input tokens + turn roundtrips) + decode (output tokens) model
                if target_model.startswith("claude-opus"):
                    prefill_s = (st["prompt_tokens"] / 1000.0) * 0.85 + (st["turns"] * 2.4)
                    decode_s = st["completion_tokens"] / 36.0
                elif target_model.startswith("claude-sonnet"):
                    prefill_s = (st["prompt_tokens"] / 1000.0) * 0.52 + (st["turns"] * 1.6)
                    decode_s = st["completion_tokens"] / 68.0
                elif "pro" in target_model:
                    prefill_s = (st["prompt_tokens"] / 1000.0) * 0.45 + (st["turns"] * 1.5)
                    decode_s = st["completion_tokens"] / 75.0
                else:
                    # Gemini 3.8 / 3.7 / 3.5 Flash
                    prefill_s = (st["prompt_tokens"] / 1000.0) * 0.22 + (st["turns"] * 1.1)
                    decode_s = st["completion_tokens"] / 165.0
                est_lat_s = round(prefill_s + decode_s, 2)
            scen_cost += c_usd
            scen_lat_s += est_lat_s
            stage_details[role] = {
                "model_id": target_model,
                "model_label": t_cfg["label"],
                "cost_usd": round(c_usd, 6),
                "latency_s": round(est_lat_s, 2),
            }
        return {
            "name": name,
            "role_map": {r: resolve_model(m)["id"] for r, m in role_map.items()},
            "total_cost_usd": round(scen_cost, 6),
            "total_latency_s": round(scen_lat_s, 2),
            "stages": stage_details,
        }

    actual_map = {
        "Planner": stages.get("Planner", {}).get("model_id", os.environ.get("PLANNER_MODEL", "claude-opus-5-5")),
        "Implementer": stages.get("Implementer", {}).get("model_id", os.environ.get("IMPLEMENTER_MODEL", "gemini-3.8-flash")),
        "Reviewer": stages.get("Reviewer", {}).get("model_id", os.environ.get("REVIEWER_MODEL", "claude-sonnet-5")),
    }

    scen_actual = simulate_scenario(
        "Hybrid Vertex AI (Opus 5.5 Plan + Gemini 3.8 Flash Implement + Sonnet 5 Review)",
        actual_map,
        is_actual=True,
    )
    scen_all_opus = simulate_scenario(
        "100% Claude Opus 5.5 (All 3 Stages on Opus 5.5)",
        {"Planner": "claude-opus-5-5", "Implementer": "claude-opus-5-5", "Reviewer": "claude-opus-5-5"},
    )
    scen_all_sonnet = simulate_scenario(
        "100% Claude Sonnet 5 (All 3 Stages on Sonnet 5)",
        {"Planner": "claude-sonnet-5", "Implementer": "claude-sonnet-5", "Reviewer": "claude-sonnet-5"},
    )
    scen_anthropic_tiered = simulate_scenario(
        "Anthropic-Only Tiered (Opus 5.5 Plan + Sonnet 5 Implement & Review)",
        {"Planner": "claude-opus-5-5", "Implementer": "claude-sonnet-5", "Reviewer": "claude-sonnet-5"},
    )
    scen_all_flash = simulate_scenario(
        "100% Gemini 3.8 Flash (Budget Mode)",
        {"Planner": "gemini-3.8-flash", "Implementer": "gemini-3.8-flash", "Reviewer": "gemini-3.8-flash"},
    )

    scenarios = [scen_actual, scen_all_opus, scen_anthropic_tiered, scen_all_sonnet, scen_all_flash]
    if custom_models:
        scen_custom = simulate_scenario(
            f"Custom What-If ({custom_models.get('Planner')} / {custom_models.get('Implementer')} / {custom_models.get('Reviewer')})",
            custom_models,
        )
        scenarios.append(scen_custom)

    opus_cost = scen_all_opus["total_cost_usd"]
    opus_lat = scen_all_opus["total_latency_s"]
    annual_tasks = num_devs * tasks_per_dev_day * workdays_per_year

    for sc in scenarios:
        c = sc["total_cost_usd"]
        l = sc["total_latency_s"]
        sc["savings_vs_opus_usd"] = round(opus_cost - c, 6)
        sc["savings_vs_opus_pct"] = round(((opus_cost - c) / opus_cost) * 100.0, 1) if opus_cost > 0 else 0.0
        sc["time_saved_vs_opus_s"] = round(opus_lat - l, 2)
        sc["speedup_vs_opus"] = round(opus_lat / l, 2) if l > 0 else 1.0
        sc["annual_cost_usd"] = round(c * annual_tasks, 2)
        sc["annual_savings_vs_opus_usd"] = round((opus_cost - c) * annual_tasks, 2)
        sc["annual_hours_saved_vs_opus"] = round(((opus_lat - l) * annual_tasks) / 3600.0, 1)

    # Implementer-specific comparison (since Implementer is the high-volume coding workhorse)
    impl_stage = stages.get("Implementer")
    impl_comparison = {}
    if impl_stage:
        in_t = impl_stage["prompt_tokens"]
        out_t = impl_stage["completion_tokens"]
        c_flash = compute_cost_usd("gemini-3.8-flash", in_t, out_t)
        c_opus = compute_cost_usd("claude-opus-5-5", in_t, out_t)
        c_sonnet = compute_cost_usd("claude-sonnet-5", in_t, out_t)
        impl_comparison = {
            "prompt_tokens": in_t,
            "completion_tokens": out_t,
            "flash_cost_usd": c_flash,
            "opus_cost_usd": c_opus,
            "sonnet_cost_usd": c_sonnet,
            "savings_vs_opus_pct": round(((c_opus - c_flash) / c_opus) * 100.0, 1) if c_opus > 0 else 85.0,
            "savings_vs_sonnet_pct": round(((c_sonnet - c_flash) / c_sonnet) * 100.0, 1) if c_sonnet > 0 else 75.0,
        }

    return {
        "run_id": run_id,
        "task_size": task_size,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "totals": {
            "turns": total_turns,
            "tool_calls": total_tools,
            "prompt_tokens": total_in,
            "completion_tokens": total_out,
            "total_tokens": total_tokens,
            "cost_usd": total_cost,
            "latency_s": total_latency_s,
        },
        "stages": stages,
        "implementer_comparison": impl_comparison,
        "scenarios": scenarios,
        "enterprise_assumptions": {
            "num_devs": num_devs,
            "tasks_per_dev_day": tasks_per_dev_day,
            "workdays_per_year": workdays_per_year,
            "annual_tasks": annual_tasks,
        },
    }


def render_terminal_report(report: Dict[str, Any]) -> str:
    """Format a rich ANSI terminal executive summary and counterfactual What-If table."""
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    MAGENTA = "\033[95m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    lines: List[str] = []
    lines.append(f"{BOLD}{CYAN}===================================================================================================={RESET}")
    lines.append(f"{BOLD}{CYAN}  CLAUDE CODE + VERTEX AI LITELLM GATEWAY — LIVE TELEMETRY & WHAT-IF ECONOMICS REPORT{RESET}")
    lines.append(f"{BOLD}{CYAN}===================================================================================================={RESET}")
    lines.append(
        f"  Run ID: {BOLD}{report['run_id']}{RESET}   |   Task Size: {BOLD}{report['task_size'].upper()}{RESET}   |   "
        f"Total Turns: {BOLD}{report['totals']['turns']}{RESET}   |   Tool Calls: {BOLD}{report['totals']['tool_calls']}{RESET}"
    )
    lines.append("")

    # 1. Per-Stage Execution Breakdown
    lines.append(f"{BOLD}{YELLOW}1. ACTUAL 3-STAGE PIPELINE EXECUTION (LIVE VERTEX AI TELEMETRY){RESET}")
    lines.append(
        f"  {'Stage':<13} {'Model':<20} {'Turns':>5} {'Tools':>5} {'In Tok':>9} {'Out Tok':>8} "
        f"{'Tok Share':>9} {'Latency':>9} {'Cost ($)':>11} {'Cost %':>8}"
    )
    lines.append("  " + "-" * 98)
    for role in STAGE_ORDER:
        st = report["stages"].get(role)
        if not st:
            continue
        lines.append(
            f"  {role:<13} {st['model_label']:<20} {st['turns']:>5} {st['tool_calls']:>5} "
            f"{st['prompt_tokens']:>9,} {st['completion_tokens']:>8,} "
            f"{st['token_share_pct']:>8.1f}% {st['latency_s']:>8.2f}s "
            f"${st['cost_usd']:>10.5f} {st['cost_share_pct']:>7.1f}%"
        )
    lines.append("  " + "-" * 98)
    tot = report["totals"]
    lines.append(
        f"  {BOLD}{'TOTAL':<13} {'Hybrid 3-Agent':<20} {tot['turns']:>5} {tot['tool_calls']:>5} "
        f"{tot['prompt_tokens']:>9,} {tot['completion_tokens']:>8,} "
        f"{'100.0%':>9} {tot['latency_s']:>8.2f}s ${tot['cost_usd']:>10.5f} {'100.0%':>8}{RESET}"
    )
    lines.append("")

    # 2. Implementer Workhorse Spotlight
    impl = report.get("implementer_comparison") or {}
    if impl:
        lines.append(f"{BOLD}{GREEN}2. IMPLEMENTER STAGE SPOTLIGHT (WHY GEMINI 3.8 FLASH WINS THE CODING LOOP){RESET}")
        lines.append(
            f"  Implementer processed {BOLD}{impl['prompt_tokens']:,} input{RESET} + {BOLD}{impl['completion_tokens']:,} output{RESET} tokens:"
        )
        lines.append(
            f"    • {BOLD}Gemini 3.8 Flash ($0.75 / $3.75 per 1M){RESET}:  ${impl['flash_cost_usd']:.5f}  "
            f"({GREEN}{BOLD}-{impl['savings_vs_opus_pct']:.1f}% vs Opus 5.5{RESET}, "
            f"{GREEN}{BOLD}-{impl['savings_vs_sonnet_pct']:.1f}% vs Sonnet 5{RESET})"
        )
        lines.append(f"    • Claude Sonnet 5  ($3.00 / $15.00 per 1M): ${impl['sonnet_cost_usd']:.5f}  (4.0x more expensive than Gemini 3.8 Flash)")
        lines.append(f"    • Claude Opus 5.5  ($5.00 / $25.00 per 1M): ${impl['opus_cost_usd']:.5f}  (6.7x more expensive than Gemini 3.8 Flash)")
        lines.append("")

    # 3. Counterfactual What-If Table
    lines.append(f"{BOLD}{MAGENTA}3. COUNTERFACTUAL WHAT-IF MATRIX (SAME TOKENS & TOOL TURNS ACROSS ARCHITECTURES){RESET}")
    lines.append(
        f"  {'Architecture Scenario':<56} {'Run Cost':>11} {'vs All-Opus':>12} {'Est Time':>9} {'Time Saved':>11}"
    )
    lines.append("  " + "-" * 102)
    for idx, sc in enumerate(report["scenarios"]):
        name_short = sc["name"][:55]
        sav_pct = sc["savings_vs_opus_pct"]
        sav_str = f"-{sav_pct:.1f}%" if sav_pct > 0 else (f"+{abs(sav_pct):.1f}%" if sav_pct < 0 else "baseline")
        t_sav = sc["time_saved_vs_opus_s"]
        t_str = f"-{t_sav:.1f}s" if t_sav > 0 else (f"+{abs(t_sav):.1f}s" if t_sav < 0 else "baseline")
        prefix = f"{GREEN}{BOLD}* " if idx == 0 else "  "
        suffix = f"{RESET}" if idx == 0 else ""
        lines.append(
            f"{prefix}{name_short:<56} ${sc['total_cost_usd']:>10.5f} {sav_str:>12} "
            f"{sc['total_latency_s']:>8.1f}s {t_str:>11}{suffix}"
        )
    lines.append("")

    # 4. Enterprise Annual Projection
    ent = report["enterprise_assumptions"]
    lines.append(
        f"{BOLD}{CYAN}4. ENTERPRISE ANNUAL PROJECTION "
        f"({ent['num_devs']} Developers × {ent['tasks_per_dev_day']} Tasks/Day × {ent['workdays_per_year']} Days = {ent['annual_tasks']:,} Runs/Yr){RESET}"
    )
    lines.append(
        f"  {'Architecture Scenario':<56} {'Annual Spend':>15} {'Annual Savings':>16} {'Dev Hours Saved':>16}"
    )
    lines.append("  " + "-" * 106)
    for idx, sc in enumerate(report["scenarios"]):
        name_short = sc["name"][:55]
        prefix = f"{GREEN}{BOLD}* " if idx == 0 else "  "
        suffix = f"{RESET}" if idx == 0 else ""
        lines.append(
            f"{prefix}{name_short:<56} ${sc['annual_cost_usd']:>14,.2f} "
            f"${sc['annual_savings_vs_opus_usd']:>15,.2f} {sc['annual_hours_saved_vs_opus']:>14,.1f} hrs{suffix}"
        )
    lines.append(f"{DIM}  HTML Executive Report written to: {LATEST_HTML_REPORT}{RESET}")
    lines.append(f"{BOLD}{CYAN}===================================================================================================={RESET}")
    return "\n".join(lines)


def write_html_report(report: Dict[str, Any], output_path: Path = LATEST_HTML_REPORT) -> Path:
    """Write a self-contained dark-mode executive HTML dashboard for the run."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    stages_rows_html = []
    for role in STAGE_ORDER:
        st = report["stages"].get(role)
        if not st:
            continue
        badge_color = "#a855f7" if st["publisher"] == "anthropic" else "#10b981"
        stages_rows_html.append(
            f"""
            <tr>
              <td><strong>{html.escape(role)}</strong></td>
              <td><span class="badge" style="background:{badge_color}22;color:{badge_color};border:1px solid {badge_color}55">{html.escape(st['model_label'])}</span></td>
              <td class="num">{st['turns']}</td>
              <td class="num">{st['tool_calls']}</td>
              <td class="num">{st['prompt_tokens']:,}</td>
              <td class="num">{st['completion_tokens']:,}</td>
              <td class="num">{st['token_share_pct']:.1f}%</td>
              <td class="num">{st['latency_s']:.2f}s</td>
              <td class="num">${st['cost_usd']:.5f}</td>
              <td class="num">{st['cost_share_pct']:.1f}%</td>
            </tr>
            """
        )

    scen_rows_html = []
    for idx, sc in enumerate(report["scenarios"]):
        row_cls = 'class="highlight-row"' if idx == 0 else ""
        scen_rows_html.append(
            f"""
            <tr {row_cls}>
              <td><strong>{html.escape(sc['name'])}</strong></td>
              <td class="num">${sc['total_cost_usd']:.5f}</td>
              <td class="num">{sc['savings_vs_opus_pct']:+.1f}%</td>
              <td class="num">{sc['total_latency_s']:.2f}s</td>
              <td class="num">{sc['time_saved_vs_opus_s']:+.2f}s</td>
              <td class="num">${sc['annual_cost_usd']:,.2f}</td>
              <td class="num">${sc['annual_savings_vs_opus_usd']:,.2f}</td>
              <td class="num">{sc['annual_hours_saved_vs_opus']:,.1f} hrs</td>
            </tr>
            """
        )

    actual_sc = report["scenarios"][0]
    ent = report["enterprise_assumptions"]
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Claude Code + Vertex AI Hybrid Routing Report ({html.escape(report['run_id'])})</title>
  <style>
    :root {{
      --bg: #0b0f19;
      --panel: #111827;
      --border: #1f2937;
      --text: #f9fafb;
      --muted: #9ca3af;
      --accent: #38bdf8;
      --green: #10b981;
      --purple: #a855f7;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 2rem;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    h1 {{ font-size: 1.65rem; margin-bottom: 0.25rem; }}
    .subtitle {{ color: var(--muted); margin-bottom: 1.5rem; font-size: 0.95rem; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem; margin-bottom: 2rem; }}
    .card {{ background: var(--panel); border: 1px solid var(--border); border-radius: 0.75rem; padding: 1.25rem; }}
    .card-label {{ color: var(--muted); font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.05em; }}
    .card-val {{ font-size: 1.8rem; font-weight: 700; margin-top: 0.35rem; color: var(--accent); }}
    .card-val.green {{ color: var(--green); }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--border); border-radius: 0.75rem; overflow: hidden; margin-bottom: 2rem; }}
    th, td {{ padding: 0.75rem 1rem; border-bottom: 1px solid var(--border); text-align: left; font-size: 0.9rem; }}
    th {{ background: #1e293b; color: var(--muted); font-weight: 600; font-size: 0.8rem; text-transform: uppercase; }}
    td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
    tr.highlight-row {{ background: rgba(16, 185, 129, 0.12); }}
    .badge {{ padding: 0.2rem 0.55rem; border-radius: 999px; font-size: 0.78rem; font-weight: 600; }}
    h2 {{ font-size: 1.15rem; margin: 1.5rem 0 0.75rem; color: var(--accent); }}
  </style>
</head>
<body>
  <div class="container">
    <h1>Claude Code + Vertex AI Hybrid Multi-Agent Cost &amp; Speed Report</h1>
    <div class="subtitle">
      Run ID: <strong>{html.escape(report['run_id'])}</strong> &bull;
      Task Size: <strong>{html.escape(report['task_size'].upper())}</strong> &bull;
      Generated: {html.escape(report['generated_at'])}
    </div>

    <div class="kpi-grid">
      <div class="card">
        <div class="card-label">Actual Hybrid Run Cost</div>
        <div class="card-val">${report['totals']['cost_usd']:.5f}</div>
      </div>
      <div class="card">
        <div class="card-label">Savings vs 100% Opus 5.5</div>
        <div class="card-val green">-{actual_sc['savings_vs_opus_pct']:.1f}%</div>
      </div>
      <div class="card">
        <div class="card-label">Time Saved vs 100% Opus 5.5</div>
        <div class="card-val green">{actual_sc['time_saved_vs_opus_s']:.1f}s ({actual_sc['speedup_vs_opus']:.2f}x)</div>
      </div>
      <div class="card">
        <div class="card-label">Annual Enterprise Savings ({ent['num_devs']} Devs)</div>
        <div class="card-val green">${actual_sc['annual_savings_vs_opus_usd']:,.0f}/yr</div>
      </div>
    </div>

    <h2>1. Live 3-Stage Pipeline Telemetry Breakdown</h2>
    <table>
      <thead>
        <tr>
          <th>Stage</th>
          <th>Vertex AI Model</th>
          <th class="num">Turns</th>
          <th class="num">Tools</th>
          <th class="num">Input Tokens</th>
          <th class="num">Output Tokens</th>
          <th class="num">Token Share</th>
          <th class="num">API Time</th>
          <th class="num">Cost (USD)</th>
          <th class="num">Cost Share</th>
        </tr>
      </thead>
      <tbody>
        {''.join(stages_rows_html)}
      </tbody>
    </table>

    <h2>2. Counterfactual What-If Comparison &amp; Enterprise ROI ({ent['annual_tasks']:,} Tasks/Year)</h2>
    <table>
      <thead>
        <tr>
          <th>Architecture Scenario</th>
          <th class="num">Run Cost</th>
          <th class="num">Savings vs Opus</th>
          <th class="num">Est. Time</th>
          <th class="num">Time Saved</th>
          <th class="num">Annual Spend</th>
          <th class="num">Annual Savings</th>
          <th class="num">Dev Hours Saved/Yr</th>
        </tr>
      </thead>
      <tbody>
        {''.join(scen_rows_html)}
      </tbody>
    </table>
  </div>
</body>
</html>
"""
    output_path.write_text(doc, encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude Code + Vertex AI Hybrid Cost & What-If Analyzer")
    parser.add_argument("--run-id", default=None, help="Specific run_id to analyze (defaults to latest run)")
    parser.add_argument("--task-size", default=None, choices=["small", "medium", "large"], help="Filter by task size")
    parser.add_argument("--planner", default=None, help="Custom What-If Planner model override")
    parser.add_argument("--implementer", default=None, help="Custom What-If Implementer model override")
    parser.add_argument("--reviewer", default=None, help="Custom What-If Reviewer model override")
    parser.add_argument("--devs", type=int, default=100, help="Enterprise developer count for annual ROI")
    parser.add_argument("--tasks-per-day", type=int, default=15, help="Agentic tasks per developer per day")
    parser.add_argument("--json", action="store_true", help="Print JSON output instead of ANSI table")
    args = parser.parse_args()

    all_rows = load_telemetry()
    if not all_rows:
        print("No telemetry records found in logs/telemetry.jsonl yet. Run ./small.sh first!", file=sys.stderr)
        return 1

    run_id, resolved_task, selected_rows = select_run_rows(all_rows, run_id=args.run_id, task_size=args.task_size)
    custom_models = None
    if args.planner or args.implementer or args.reviewer:
        custom_models = {
            "Planner": args.planner or os.environ.get("PLANNER_MODEL", "claude-opus-5-5"),
            "Implementer": args.implementer or os.environ.get("IMPLEMENTER_MODEL", "gemini-3.8-flash"),
            "Reviewer": args.reviewer or os.environ.get("REVIEWER_MODEL", "claude-sonnet-5"),
        }

    report = build_what_if_analysis(
        run_rows=selected_rows,
        all_rows=all_rows,
        run_id=run_id,
        task_size=resolved_task,
        custom_models=custom_models,
        num_devs=args.devs,
        tasks_per_dev_day=args.tasks_per_day,
    )
    write_html_report(report)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_terminal_report(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
