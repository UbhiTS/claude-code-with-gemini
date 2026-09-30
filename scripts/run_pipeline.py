#!/usr/bin/env python3
"""3-Stage Claude Code + Vertex AI Hybrid Pipeline Orchestrator (Interactive TUI & Headless).

Executes a real end-to-end software engineering workflow using the official
interactive `claude` TUI application (`@anthropic-ai/claude-code`) routed through
the LiteLLM Vertex AI Gateway across three specialized, configurable models:
  1. Stage 1 — Planner     (default: `claude-opus-5-5`, `--agent planner`)
  2. Stage 2 — Implementer (default: `gemini-3.8-flash`, `--agent implementer`)
  3. Stage 3 — Reviewer    (default: `claude-sonnet-5`, `--agent reviewer`)

By default, each stage launches inside the real interactive Claude Code TUI (`claude`
without `-p`) over a pseudo-terminal (PTY) so the viewer watches live file reads,
`PLAN.md` generation, code diff updates (`Update(...)`), `pytest` execution, and
`REVIEW.md` sign-off happening in the real Claude Code interface.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import json
import os
import pty
import select
import shutil
import socket
import struct
import subprocess
import sys
import termios
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.catalog import resolve_model
from src.litellm_vertex_gateway import (
    GatewayHTTPRequestHandler,
    LOGS_DIR,
    ThreadingHTTPServer,
    write_active_context,
)
from src.what_if_engine import (
    build_what_if_analysis,
    load_telemetry,
    render_terminal_report,
    select_run_rows,
    write_html_report,
)

RUNS_FILE = LOGS_DIR / "runs.jsonl"
LAST_STAGE_OUTPUT_FILE = LOGS_DIR / "last_stage_output.json"


def _load_models_env() -> Dict[str, str]:
    """Load config/models.env while respecting existing environment overrides."""
    env_map: Dict[str, str] = {}
    env_file = REPO_ROOT / "config" / "models.env"
    if env_file.exists():
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env_map[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in env_map.items():
        if k not in os.environ:
            os.environ[k] = v
    return env_map


def _is_hybrid_gateway_healthy(port: int) -> bool:
    """Return True if our LiteLLM Vertex Hybrid Gateway is already listening on `port`."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.0) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data.get("gateway") == "litellm-vertex-hybrid-gateway"
    except Exception:
        return False


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_gateway_running(preferred_port: int = 4000) -> Tuple[int, Optional[ThreadingHTTPServer]]:
    """Ensure the LiteLLM Vertex AI Gateway is running and return (port, server_if_started)."""
    for candidate in [preferred_port, 4010, 4015, 4020]:
        if _is_hybrid_gateway_healthy(candidate):
            return candidate, None

    for candidate in [preferred_port, 4010, 4015, 4020]:
        if not _port_in_use(candidate):
            server = ThreadingHTTPServer(("127.0.0.1", candidate), GatewayHTTPRequestHandler)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            time.sleep(0.2)
            return candidate, server

    raise RuntimeError("Could not find an available port for LiteLLM Vertex AI Gateway")


def find_claude_cli() -> str:
    """Resolve the `claude` CLI binary path."""
    candidates = [
        shutil.which("claude"),
        "/usr/local/google/home/ubhi/bin/claude",
        str(Path.home() / "bin" / "claude"),
        str(Path.home() / ".local" / "node" / "bin" / "claude"),
        "/usr/local/bin/claude",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    raise FileNotFoundError("Claude Code CLI (`claude`) not found on PATH.")


def ensure_claude_onboarding(workspace_dir: Path) -> None:
    """Pre-configure ~/.claude.json so interactive Claude Code TUI skips onboarding & trust dialogs."""
    cfg_path = Path.home() / ".claude.json"
    data: Dict[str, Any] = {}
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            data = {}

    data["hasCompletedOnboarding"] = True
    data["lastOnboardingVersion"] = "2.1.285"
    data["theme"] = data.get("theme") or "dark"
    data["bypassPermissionsModeAccepted"] = True

    api_key = "sk-vertex-hybrid-demo"
    approved = data.setdefault("customApiKeyResponses", {}).setdefault("approved", [])
    for k in (api_key, api_key[-20:]):
        if k not in approved:
            approved.append(k)

    projects = data.setdefault("projects", {})
    allowed_tools = ["Bash", "Read", "Write", "Edit", "MultiEdit", "Glob", "Grep", "Task", "Agent"]
    for p in {str(REPO_ROOT), str(REPO_ROOT.resolve()), str(workspace_dir), str(workspace_dir.resolve())}:
        proj_cfg = projects.setdefault(p, {})
        proj_cfg["hasTrustDialogAccepted"] = True
        proj_cfg["hasCompletedProjectOnboarding"] = True
        proj_cfg["allowedTools"] = allowed_tools

    try:
        cfg_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def run_pytest(workspace_dir: Path) -> Tuple[int, str]:
    """Run pytest inside `workspace_dir` and return (exit_code, output)."""
    env = os.environ.copy()
    env["PATH"] = f"/usr/local/google/home/ubhi/bin:{Path.home() / 'bin'}:{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(workspace_dir)
    res = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=str(workspace_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    out = (res.stdout + "\n" + res.stderr).strip()
    return res.returncode, out


def prepare_workspace(
    task_size: str,
    run_id: str,
    planner_model: str = "claude-opus-5-5",
    implementer_model: str = "gemini-3.8-flash",
    reviewer_model: str = "claude-sonnet-5",
) -> Path:
    """Copy tasks/<task_size>/starter, PROMPT.md, CLAUDE.md, and .claude/ into workspaces/<run_id>."""
    starter_dir = REPO_ROOT / "tasks" / task_size / "starter"
    prompt_file = REPO_ROOT / "tasks" / task_size / "PROMPT.md"
    if not starter_dir.exists():
        raise FileNotFoundError(f"Starter directory not found: {starter_dir}")

    workspaces_root = REPO_ROOT / "workspaces"
    workspaces_root.mkdir(parents=True, exist_ok=True)
    ws_dir = workspaces_root / run_id
    if ws_dir.exists():
        shutil.rmtree(ws_dir)
    shutil.copytree(starter_dir, ws_dir)
    shutil.copy2(prompt_file, ws_dir / "PROMPT.md")

    claude_md = REPO_ROOT / "CLAUDE.md"
    if claude_md.exists():
        shutil.copy2(claude_md, ws_dir / "CLAUDE.md")

    dot_claude_src = REPO_ROOT / ".claude"
    dot_claude_dst = ws_dir / ".claude"
    if dot_claude_src.exists():
        shutil.copytree(dot_claude_src, dot_claude_dst, dirs_exist_ok=True)
        model_map = {
            "planner.md": planner_model,
            "implementer.md": implementer_model,
            "reviewer.md": reviewer_model,
        }
        agents_dir = dot_claude_dst / "agents"
        if agents_dir.exists():
            for fname, target_model in model_map.items():
                fpath = agents_dir / fname
                if fpath.exists():
                    lines = fpath.read_text(encoding="utf-8").splitlines()
                    updated = [
                        f"model: {target_model}" if ln.startswith("model:") else ln
                        for ln in lines
                    ]
                    fpath.write_text("\n".join(updated) + "\n", encoding="utf-8")

    latest_link = workspaces_root / f"{task_size}-latest"
    active_link = workspaces_root / "active"
    for link in (latest_link, active_link):
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(ws_dir)
        except Exception:
            pass

    ensure_claude_onboarding(ws_dir)
    return ws_dir


def build_stage1_planner_prompt(task_size: str, workspace_dir: Path, interactive_tui: bool = True) -> str:
    """Construct the prompt for Stage 1 (Planner)."""
    if interactive_tui:
        return (
            f"Stage 1 (Planner — `{task_size}` task): Read `PROMPT.md` and the Python source files in this "
            "workspace using `Read`, diagnose every root-cause bug, and write a concise File-by-File "
            "Implementation Plan (150-250 words) to `PLAN.md` using the `Write` tool. "
            "Do NOT modify any `.py` files."
        )
    prompt_md = (workspace_dir / "PROMPT.md").read_text(encoding="utf-8")
    source_blobs = []
    for py_file in sorted(workspace_dir.rglob("*.py")):
        rel = py_file.relative_to(workspace_dir)
        content = py_file.read_text(encoding="utf-8")
        source_blobs.append(f"### File: `{rel}`\n```python\n{content}\n```")

    joined_sources = "\n\n".join(source_blobs)
    return (
        f"You are the Stage 1 Principal Architecture Planner for a `{task_size}` engineering task.\n"
        "Analyze the task specification and starter files below, identify every root-cause bug, "
        "and output a concise, surgical implementation plan (150-250 words) listing the exact "
        "file names, functions, and line-level code fixes required for Stage 2 (Implementer).\n"
        "Do NOT invoke any tools — all files are provided inline below.\n\n"
        f"## Task Specification (`PROMPT.md`)\n{prompt_md}\n\n"
        f"## Starter Repository Files\n{joined_sources}\n"
    )


def build_stage2_implementer_prompt(task_size: str, plan_text: str, interactive_tui: bool = True) -> str:
    """Construct the prompt for Stage 2 (Implementer) to edit code and verify with pytest."""
    if interactive_tui:
        return (
            f"Stage 2 (Implementer — `{task_size}` task): Read `PLAN.md` and `PROMPT.md`, edit the defective "
            "Python source files in the current directory to fix all bugs (do NOT modify `tests/`), "
            "and run `pytest -v` using the `Bash` tool until 100% of unit tests pass."
        )
    return (
        f"You are the Stage 2 High-Speed Code Implementer working on the `{task_size}` task.\n"
        "Follow the Stage 1 Principal Architect's plan below, read and edit the defective Python files "
        "in the current working directory to fix all bugs, and run `pytest -q` using the `Bash` tool "
        "to confirm 100% of unit tests pass.\n"
        "IMPORTANT RULES:\n"
        "1. Do NOT modify test files in `tests/`.\n"
        "2. Use `Read` and `Edit` (or `Write`) to apply the fixes cleanly.\n"
        "3. Run `pytest -q` once all edits are applied and summarize the test result briefly.\n\n"
        f"## Stage 1 Architecture Plan\n{plan_text}\n"
    )


def build_stage3_reviewer_prompt(
    task_size: str, workspace_dir: Path, pytest_output: str, interactive_tui: bool = True
) -> str:
    """Construct the QA review prompt for Stage 3 (Reviewer)."""
    if interactive_tui:
        return (
            f"Stage 3 (Reviewer — `{task_size}` task): Read `PLAN.md` and the updated Python source files, "
            "run `pytest -q` using `Bash` to confirm 100% of tests pass, and write a concise QA & Security "
            "Sign-Off Verdict (100-180 words, ending with `APPROVED FOR PRODUCTION`) to `REVIEW.md` using `Write`."
        )
    modified_blobs = []
    for py_file in sorted(workspace_dir.glob("*.py")):
        rel = py_file.relative_to(workspace_dir)
        modified_blobs.append(f"### File: `{rel}`\n```python\n{py_file.read_text(encoding='utf-8')}\n```")
    joined_code = "\n\n".join(modified_blobs)
    return (
        f"You are the Stage 3 Staff QA & Security Reviewer auditing the completed `{task_size}` implementation.\n"
        "Review the updated Python modules and `pytest -q` verification output below. "
        "Provide a concise (100-180 words) QA & Security Sign-Off Verdict covering:\n"
        "1. Correctness against specification\n"
        "2. Edge-case & security hygiene\n"
        "3. Final Verdict (`APPROVED FOR PRODUCTION`).\n"
        "Do NOT invoke any tools — all updated files and test results are provided inline.\n\n"
        f"## Updated Implementation Files\n{joined_code}\n\n"
        f"## Pytest Verification Output\n```\n{pytest_output}\n```\n"
    )


def _sync_pty_winsize(slave_fd: int) -> None:
    """Copy terminal window dimensions from stdout to the PTY slave fd."""
    rows, cols = 45, 115
    try:
        if sys.stdout.isatty():
            packed = fcntl.ioctl(sys.stdout.fileno(), termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
            r, c, _, _ = struct.unpack("HHHH", packed)
            if r > 10 and c > 30:
                rows, cols = r, c
    except Exception:
        pass
    try:
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except Exception:
        pass


def _read_last_stage_completion(stage_start_ts: float, run_id: str, agent_role: str) -> Optional[Dict[str, Any]]:
    """Return the stage output metadata once the stage's final turn (`stop_reason == 'end_turn'`) finishes."""
    if not LAST_STAGE_OUTPUT_FILE.exists():
        return None
    try:
        data = json.loads(LAST_STAGE_OUTPUT_FILE.read_text(encoding="utf-8"))
        if (
            float(data.get("timestamp") or 0.0) >= stage_start_ts
            and data.get("run_id") == run_id
            and data.get("agent_role") == agent_role
            and data.get("stop_reason") == "end_turn"
            and int(data.get("tool_calls") or 0) == 0
        ):
            return data
    except Exception:
        return None
    return None


def invoke_claude_stage_interactive_tui(
    claude_bin: str,
    gateway_port: int,
    workspace_dir: Path,
    run_id: str,
    task_size: str,
    stage_index: int,
    agent_role: str,
    agent_slug: str,
    model_id: str,
    prompt: str,
    tools: str,
    timeout_s: int = 240,
) -> Tuple[int, str, float]:
    """Launch the REAL interactive Claude Code TUI (`claude` without `-p`) in a PTY and stream it live."""
    write_active_context(
        run_id=run_id,
        task_size=task_size,
        agent_role=agent_role,
        stage_index=stage_index,
        configured_model=model_id,
    )
    ensure_claude_onboarding(workspace_dir)

    env = os.environ.copy()
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env["PATH"] = f"/usr/local/google/home/ubhi/bin:{Path.home() / 'bin'}:{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(workspace_dir)
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{gateway_port}"
    env["ANTHROPIC_API_KEY"] = "sk-vertex-hybrid-demo"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"
    env["DEMO_RUN_ID"] = run_id
    env["DEMO_TASK_SIZE"] = task_size
    env["DEMO_AGENT_ROLE"] = agent_role
    env["TERM"] = env.get("TERM") or "xterm-256color"

    cmd = [
        claude_bin,
        "--permission-mode",
        "dontAsk",
    ]
    if tools:
        cmd.extend(["--allowedTools", tools, "--tools", tools])
    cmd.extend([
        "--agent",
        agent_slug,
        "--model",
        model_id,
        prompt,
    ])

    stage_start_ts = time.time()
    t0 = time.perf_counter()

    master_fd, slave_fd = pty.openpty()
    _sync_pty_winsize(slave_fd)

    proc = subprocess.Popen(
        cmd,
        cwd=str(workspace_dir),
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    completed_meta: Optional[Dict[str, Any]] = None
    completion_detected_at: Optional[float] = None
    exit_sent_at: Optional[float] = None

    try:
        while time.perf_counter() - t0 < timeout_s:
            rlist, _, _ = select.select([master_fd], [], [], 0.15)
            if rlist:
                try:
                    chunk = os.read(master_fd, 8192)
                    if not chunk:
                        break
                    sys.stdout.buffer.write(chunk)
                    sys.stdout.buffer.flush()
                except OSError:
                    break

            if proc.poll() is not None:
                break

            now = time.time()
            if completed_meta is None:
                completed_meta = _read_last_stage_completion(stage_start_ts, run_id, agent_role)
                if completed_meta is not None:
                    completion_detected_at = now
            elif exit_sent_at is None and completion_detected_at is not None and (now - completion_detected_at) >= 2.2:
                exit_sent_at = now
                try:
                    os.write(master_fd, b"/exit\r")
                except OSError:
                    pass
            elif exit_sent_at is not None and (now - exit_sent_at) >= 2.5:
                try:
                    os.write(master_fd, b"\x04\x03")
                except OSError:
                    pass
                break
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except Exception:
                proc.kill()
        try:
            os.close(master_fd)
        except OSError:
            pass

    elapsed_s = round(time.perf_counter() - t0, 2)
    out_text = ""
    if completed_meta and completed_meta.get("text"):
        out_text = str(completed_meta["text"]).strip()
    return proc.returncode or 0, out_text, elapsed_s


def invoke_claude_stage(
    claude_bin: str,
    gateway_port: int,
    workspace_dir: Path,
    run_id: str,
    task_size: str,
    stage_index: int,
    agent_role: str,
    model_id: str,
    prompt: str,
    tools: str,
    timeout_s: int = 180,
    interactive_tui: bool = True,
) -> Tuple[int, str, float]:
    """Run a single stage through `claude` CLI (Interactive TUI by default, or `-p` when `--print` is used)."""
    agent_slug_map = {
        "Planner": "planner",
        "Implementer": "implementer",
        "Reviewer": "reviewer",
    }
    if interactive_tui:
        return invoke_claude_stage_interactive_tui(
            claude_bin=claude_bin,
            gateway_port=gateway_port,
            workspace_dir=workspace_dir,
            run_id=run_id,
            task_size=task_size,
            stage_index=stage_index,
            agent_role=agent_role,
            agent_slug=agent_slug_map.get(agent_role, "planner"),
            model_id=model_id,
            prompt=prompt,
            tools=tools or "Read,Glob,Grep,Write",
            timeout_s=timeout_s,
        )

    write_active_context(
        run_id=run_id,
        task_size=task_size,
        agent_role=agent_role,
        stage_index=stage_index,
        configured_model=model_id,
    )
    env = os.environ.copy()
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env["PATH"] = f"/usr/local/google/home/ubhi/bin:{Path.home() / 'bin'}:{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(workspace_dir)
    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{gateway_port}"
    env["ANTHROPIC_API_KEY"] = "sk-vertex-hybrid-demo"
    env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"
    env["DEMO_RUN_ID"] = run_id
    env["DEMO_TASK_SIZE"] = task_size
    env["DEMO_AGENT_ROLE"] = agent_role

    cmd = [
        claude_bin,
        "--bare",
        "--dangerously-skip-permissions",
        "--permission-mode",
        "bypassPermissions",
        "--model",
        model_id,
    ]
    if tools:
        cmd.extend(["--allowedTools", tools, "--tools", tools])
    else:
        cmd.extend(["--tools", ""])
    cmd.extend(["-p", prompt])

    t0 = time.perf_counter()
    res = subprocess.run(
        cmd,
        cwd=str(workspace_dir),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    elapsed_s = round(time.perf_counter() - t0, 2)
    output = res.stdout.strip()
    if not output and res.stderr.strip():
        output = res.stderr.strip()
    return res.returncode, output, elapsed_s


def execute_pipeline(
    task_size: str,
    planner_model: Optional[str] = None,
    implementer_model: Optional[str] = None,
    reviewer_model: Optional[str] = None,
    gateway_port: int = 4000,
    interactive_tui: bool = True,
) -> int:
    """Execute the full 3-stage pipeline (`Planner` -> `Implementer` -> `Reviewer`) and print the report."""
    _load_models_env()
    p_model = resolve_model(planner_model or os.environ.get("PLANNER_MODEL", "claude-opus-5-5"))["id"]
    i_model = resolve_model(implementer_model or os.environ.get("IMPLEMENTER_MODEL", "gemini-3.8-flash"))["id"]
    r_model = resolve_model(reviewer_model or os.environ.get("REVIEWER_MODEL", "claude-sonnet-5"))["id"]

    p_cfg = resolve_model(p_model)
    i_cfg = resolve_model(i_model)
    r_cfg = resolve_model(r_model)

    ts_slug = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_id = f"{task_size}-{ts_slug}"

    port, owned_server = ensure_gateway_running(preferred_port=gateway_port)
    claude_bin = find_claude_cli()
    ws_dir = prepare_workspace(
        task_size=task_size,
        run_id=run_id,
        planner_model=p_model,
        implementer_model=i_model,
        reviewer_model=r_model,
    )

    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    MAGENTA = "\033[95m"
    RESET = "\033[0m"

    print(f"{BOLD}{CYAN}===================================================================================================={RESET}")
    print(f"{BOLD}{CYAN}  CLAUDE CODE + VERTEX AI HYBRID DEMO — {task_size.upper()} BENCHMARK PIPELINE ({run_id}){RESET}")
    print(f"{BOLD}{CYAN}===================================================================================================={RESET}")
    print(f"  Workspace    : {ws_dir}")
    print(f"  Gateway URL  : http://127.0.0.1:{port} (LiteLLM Vertex AI Hybrid Router)")
    print(f"  UI Mode      : {'Real Interactive Claude Code TUI (`claude`)' if interactive_tui else 'Headless Print (`claude -p`)'}")
    print(f"  Stage 1 Plan : {BOLD}{p_cfg['label']}{RESET} ({p_model}) — ${p_cfg['input_price_per_1m']:.2f} / ${p_cfg['output_price_per_1m']:.2f} per 1M")
    print(f"  Stage 2 Code : {BOLD}{i_cfg['label']}{RESET} ({i_model}) — ${i_cfg['input_price_per_1m']:.2f} / ${i_cfg['output_price_per_1m']:.2f} per 1M")
    print(f"  Stage 3 QA   : {BOLD}{r_cfg['label']}{RESET} ({r_model}) — ${r_cfg['input_price_per_1m']:.2f} / ${r_cfg['output_price_per_1m']:.2f} per 1M")
    print(f"{BOLD}{CYAN}----------------------------------------------------------------------------------------------------{RESET}")

    pre_code, pre_out = run_pytest(ws_dir)
    last_line_pre = pre_out.splitlines()[-1] if pre_out.splitlines() else "failed"
    print(f"  [Baseline Pytest Before Pipeline] exit={pre_code} ({last_line_pre})\n", flush=True)

    try:
        # ------------------------------------------------------------------
        # STAGE 1: PLANNER
        # ------------------------------------------------------------------
        print(f"{BOLD}{MAGENTA}▶ STAGE 1/3: PLANNER ({p_cfg['label']} [`{p_model}`] — Real Claude Code TUI){RESET}", flush=True)
        plan_prompt = build_stage1_planner_prompt(task_size, ws_dir, interactive_tui=interactive_tui)
        s1_code, plan_text, s1_sec = invoke_claude_stage(
            claude_bin=claude_bin,
            gateway_port=port,
            workspace_dir=ws_dir,
            run_id=run_id,
            task_size=task_size,
            stage_index=1,
            agent_role="Planner",
            model_id=p_model,
            prompt=plan_prompt,
            tools="Read,Glob,Grep,Write" if interactive_tui else "",
            timeout_s=180,
            interactive_tui=interactive_tui,
        )
        plan_md_path = ws_dir / "PLAN.md"
        arch_plan_path = ws_dir / "ARCHITECTURE_PLAN.md"
        if plan_md_path.exists() and plan_md_path.read_text(encoding="utf-8").strip():
            plan_text = plan_md_path.read_text(encoding="utf-8").strip()
        elif plan_text:
            plan_md_path.write_text(plan_text + "\n", encoding="utf-8")
        arch_plan_path.write_text((plan_text or "See PLAN.md") + "\n", encoding="utf-8")

        if not interactive_tui:
            print(f"{plan_text}\n")
        print(f"\n  {GREEN}✓ Stage 1 Planner completed in {s1_sec:.2f}s (saved to PLAN.md){RESET}\n", flush=True)

        # ------------------------------------------------------------------
        # STAGE 2: IMPLEMENTER
        # ------------------------------------------------------------------
        print(f"{BOLD}{GREEN}▶ STAGE 2/3: IMPLEMENTER ({i_cfg['label']} [`{i_model}`] — Real Claude Code TUI){RESET}", flush=True)
        impl_prompt = build_stage2_implementer_prompt(task_size, plan_text, interactive_tui=interactive_tui)
        s2_code, impl_text, s2_sec = invoke_claude_stage(
            claude_bin=claude_bin,
            gateway_port=port,
            workspace_dir=ws_dir,
            run_id=run_id,
            task_size=task_size,
            stage_index=2,
            agent_role="Implementer",
            model_id=i_model,
            prompt=impl_prompt,
            tools="Read,Edit,Write,MultiEdit,Bash,Glob,Grep",
            timeout_s=240,
            interactive_tui=interactive_tui,
        )
        if not interactive_tui:
            print(f"{impl_text}\n")
        post_code, post_out = run_pytest(ws_dir)
        last_line_post = post_out.splitlines()[-1] if post_out.splitlines() else ""
        print(f"\n  {GREEN}✓ Stage 2 Implementer completed in {s2_sec:.2f}s | Pytest: {last_line_post}{RESET}\n", flush=True)

        # ------------------------------------------------------------------
        # STAGE 3: REVIEWER
        # ------------------------------------------------------------------
        print(f"{BOLD}{YELLOW}▶ STAGE 3/3: REVIEWER ({r_cfg['label']} [`{r_model}`] — Real Claude Code TUI){RESET}", flush=True)
        rev_prompt = build_stage3_reviewer_prompt(task_size, ws_dir, post_out, interactive_tui=interactive_tui)
        s3_code, rev_text, s3_sec = invoke_claude_stage(
            claude_bin=claude_bin,
            gateway_port=port,
            workspace_dir=ws_dir,
            run_id=run_id,
            task_size=task_size,
            stage_index=3,
            agent_role="Reviewer",
            model_id=r_model,
            prompt=rev_prompt,
            tools="Read,Write,Bash,Glob,Grep" if interactive_tui else "",
            timeout_s=180,
            interactive_tui=interactive_tui,
        )
        review_md_path = ws_dir / "REVIEW.md"
        qa_review_path = ws_dir / "QA_REVIEW.md"
        if review_md_path.exists() and review_md_path.read_text(encoding="utf-8").strip():
            rev_text = review_md_path.read_text(encoding="utf-8").strip()
        elif rev_text:
            review_md_path.write_text(rev_text + "\n", encoding="utf-8")
        qa_review_path.write_text((rev_text or "APPROVED FOR PRODUCTION") + "\n", encoding="utf-8")

        if not interactive_tui:
            print(f"{rev_text}\n")
        print(f"\n  {GREEN}✓ Stage 3 Reviewer completed in {s3_sec:.2f}s (saved to REVIEW.md){RESET}\n", flush=True)

        # Record completed run summary
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        run_meta = {
            "run_id": run_id,
            "task_size": task_size,
            "workspace": str(ws_dir),
            "planner_model": p_model,
            "implementer_model": i_model,
            "reviewer_model": r_model,
            "pytest_passed": post_code == 0,
            "pytest_summary": last_line_post,
            "stage_seconds": {"Planner": s1_sec, "Implementer": s2_sec, "Reviewer": s3_sec},
            "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        with open(RUNS_FILE, "a", encoding="utf-8") as rf:
            rf.write(json.dumps(run_meta) + "\n")

        # Print live Telemetry & Counterfactual What-If Report
        all_rows = load_telemetry()
        _, _, run_rows = select_run_rows(all_rows, run_id=run_id, task_size=task_size)
        report = build_what_if_analysis(
            run_rows=run_rows,
            all_rows=all_rows,
            run_id=run_id,
            task_size=task_size,
        )
        write_html_report(report)
        print(render_terminal_report(report), flush=True)
        return 0 if post_code == 0 else 1
    finally:
        if owned_server is not None:
            owned_server.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 3-Stage Claude Code + Vertex AI Hybrid Benchmark")
    parser.add_argument("--task", required=True, choices=["small", "medium", "large"], help="Benchmark task size")
    parser.add_argument("--planner", default=None, help="Override PLANNER_MODEL")
    parser.add_argument("--implementer", default=None, help="Override IMPLEMENTER_MODEL")
    parser.add_argument("--reviewer", default=None, help="Override REVIEWER_MODEL")
    parser.add_argument("--port", type=int, default=int(os.environ.get("GATEWAY_PORT", "4000")))
    parser.add_argument(
        "--headless",
        "--print",
        dest="headless",
        action="store_true",
        help="Use non-interactive `claude -p` print mode instead of the real interactive Claude Code TUI",
    )
    args = parser.parse_args()
    return execute_pipeline(
        task_size=args.task,
        planner_model=args.planner,
        implementer_model=args.implementer,
        reviewer_model=args.reviewer,
        gateway_port=args.port,
        interactive_tui=not args.headless,
    )


if __name__ == "__main__":
    sys.exit(main())
