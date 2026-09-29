#!/usr/bin/env python3
"""Bottom-right tmux pane live workspace & pytest watcher.

Watches `workspaces/active` and displays modified files alongside live `pytest -q`
verification status as the Stage 2 Implementer (`gemini-3.8-flash`) edits code.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTIVE_WS = REPO_ROOT / "workspaces" / "active"


def render_workspace_status() -> str:
    BOLD = "\033[1m"
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    RED = "\033[91m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    lines = [
        f"{BOLD}{CYAN}┌──────────────────────────────────────────────────────────────────────────────┐{RESET}",
        f"{BOLD}{CYAN}│  LIVE WORKSPACE & PYTEST VERIFICATION WATCHER                                │{RESET}",
        f"{BOLD}{CYAN}└──────────────────────────────────────────────────────────────────────────────┘{RESET}",
    ]
    if not ACTIVE_WS.exists():
        lines.append(f"  {DIM}Waiting for active benchmark workspace in {ACTIVE_WS} ...{RESET}")
        return "\n".join(lines)

    target = ACTIVE_WS.resolve()
    lines.append(f"  Workspace: {BOLD}{target.name}{RESET}")
    py_files = sorted(p.relative_to(target) for p in target.rglob("*.py"))
    lines.append(f"  Files    : {', '.join(str(p) for p in py_files)}")
    lines.append("")

    env = os.environ.copy()
    env["PATH"] = f"/usr/local/google/home/ubhi/bin:{Path.home() / 'bin'}:{env.get('PATH', '')}"
    env["PYTHONPATH"] = str(target)
    try:
        res = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=str(target),
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        status_str = f"{GREEN}{BOLD}PASSING (exit=0){RESET}" if res.returncode == 0 else f"{RED}{BOLD}FAILING (exit={res.returncode}){RESET}"
        lines.append(f"  Pytest Status: {status_str}")
        lines.append("  " + "─" * 73)
        out = (res.stdout + "\n" + res.stderr).strip()
        for ln in out.splitlines()[-12:]:
            lines.append("  " + ln)
    except Exception as e:
        lines.append(f"  Pytest execution error: {e}")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Live workspace & pytest watcher")
    parser.add_argument("--once", action="store_true", help="Render once and exit")
    parser.add_argument("--interval", type=float, default=2.0, help="Poll interval in seconds")
    args = parser.parse_args()

    if args.once:
        print(render_workspace_status())
        return 0

    try:
        while True:
            frame = render_workspace_status()
            sys.stdout.write("\033[2J\033[H" + frame + "\n")
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
