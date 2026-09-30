# Claude Code + Vertex AI Hybrid Multi-Agent Architecture

This workspace uses a **3-Stage Hybrid Multi-Agent Architecture** routed through the LiteLLM Vertex AI Gateway (`http://127.0.0.1:4000`):

1. **Stage 1 — `planner` (`PLANNER_MODEL`, default `claude-opus-5-5`)**:
   - Inspects `PROMPT.md` and the starter Python codebase using `Read`, `Glob`, and `Grep`.
   - Writes a root-cause analysis and file-by-file engineering blueprint to `PLAN.md` (and `ARCHITECTURE_PLAN.md`) without modifying application code.
2. **Stage 2 — `implementer` (`IMPLEMENTER_MODEL`, default `gemini-3.8-flash`)**:
   - Reads `PLAN.md` and edits the defective Python modules using `Read`, `Edit`, and `Write`.
   - Executes `pytest -q` using `Bash` until 100% of unit tests pass.
   - Never modifies test files under `tests/`.
3. **Stage 3 — `reviewer` (`REVIEWER_MODEL`, default `claude-sonnet-5`)**:
   - Audits the implemented code and `pytest` verification results against `PLAN.md` and `PROMPT.md`.
   - Writes the QA & Security Sign-Off verdict to `REVIEW.md` (and `QA_REVIEW.md`).
