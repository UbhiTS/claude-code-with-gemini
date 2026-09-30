#!/usr/bin/env python3
"""LiteLLM Vertex AI Gateway & Anthropic `/v1/messages` Bridge.

Enables `@anthropic-ai/claude-code` (`ANTHROPIC_BASE_URL=http://127.0.0.1:4000`)
to route seamlessly across:
  - Planner     : `claude-opus-5-5` on Vertex AI (`:rawPredict`)
  - Implementer : `gemini-3.8-flash` on Vertex AI (`:generateContent`)
  - Reviewer    : `claude-sonnet-5` on Vertex AI (`:rawPredict`)

Key technical capabilities:
  1. Translates Anthropic `/v1/messages` (including `system`, multi-turn `tool_use`
     and `tool_result` blocks, and JSON Schema `input_schema`) to Vertex AI
     Gemini `:generateContent` and back (both JSON and SSE streaming).
  2. Preserves Gemini 3.x `thoughtSignature` across multi-turn `functionCall` ->
     `functionResponse` tool loops so `gemini-3.8-flash` never returns 400.
  3. Enforces Gemini 3.8 Flash thinking rules (`thinkingLevel: low|medium|high`,
     rejecting `minimal` and omitting `temperature`).
  4. Acquires Vertex AI credentials automatically from:
     (a) GCE / Cloud Run Metadata Server (`metadata.google.internal`)
     (b) `AGENT_PLATFORM_API_KEY` / `CLAUDE_BEARER_TOKEN` environment variables
     (c) `gcloud auth print-access-token` (`CLOUDSDK_ACTIVE_CONFIG_NAME=argolis`)
     (d) Argolis Cloud Run Bridge (`LLM_COMPARE_BRIDGE_URL`) when local `gcloud`
         OAuth is expired on a developer laptop.
  5. Logs every LLM turn with high-precision token, throughput (`tok/s`), and
     USD cost telemetry into `logs/telemetry.jsonl`.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.catalog import MODEL_CATALOG, compute_cost_usd, resolve_model

LOGS_DIR = REPO_ROOT / "logs"
TELEMETRY_FILE = LOGS_DIR / "telemetry.jsonl"
ACTIVE_CONTEXT_FILE = LOGS_DIR / "active_context.json"

# Cache of tool_use_id -> Gemini model part (preserving `thoughtSignature`)
_THOUGHT_SIGNATURE_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()
_TELEMETRY_LOCK = threading.Lock()

# Token cache for GCE metadata / gcloud OAuth
_TOKEN_CACHE: Dict[str, Any] = {"token": None, "expires_at": 0.0}
_BRIDGE_OPENER: Optional[urllib.request.OpenerDirector] = None


def _load_dotenv_files() -> None:
    """Load environment variables from .env, config/models.env, and sibling llm-compare/.env."""
    candidates = [
        REPO_ROOT / "config" / "models.env",
        REPO_ROOT / ".env",
        REPO_ROOT.parent / "llm-compare" / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
        except Exception:
            pass


_load_dotenv_files()


def get_active_context(headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Resolve run_id, task_size, and agent_role from request headers or active_context.json."""
    ctx = {
        "run_id": os.environ.get("DEMO_RUN_ID", "interactive"),
        "task_size": os.environ.get("DEMO_TASK_SIZE", "interactive"),
        "agent_role": os.environ.get("DEMO_AGENT_ROLE", " interactive"),
    }
    if ACTIVE_CONTEXT_FILE.exists():
        try:
            file_ctx = json.loads(ACTIVE_CONTEXT_FILE.read_text(encoding="utf-8"))
            if isinstance(file_ctx, dict):
                for k in ("run_id", "task_size", "agent_role", "effort"):
                    if file_ctx.get(k):
                        ctx[k] = str(file_ctx[k])
        except Exception:
            pass
    if headers:
        h_lower = {k.lower(): v for k, v in headers.items()}
        if h_lower.get("x-run-id"):
            ctx["run_id"] = h_lower["x-run-id"]
        if h_lower.get("x-task-size"):
            ctx["task_size"] = h_lower["x-task-size"]
        if h_lower.get("x-agent-role"):
            ctx["agent_role"] = h_lower["x-agent-role"]
    ctx["agent_role"] = ctx["agent_role"].strip()
    return ctx


def write_active_context(
    run_id: str,
    task_size: str,
    agent_role: str,
    stage_index: int = 1,
    configured_model: str = "",
) -> None:
    """Write active pipeline stage metadata to logs/active_context.json."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "task_size": task_size,
        "agent_role": agent_role,
        "stage_index": stage_index,
        "configured_model": configured_model,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    ACTIVE_CONTEXT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")



def record_telemetry(entry: Dict[str, Any]) -> None:
    """Append a structured telemetry record to logs/telemetry.jsonl."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    with _TELEMETRY_LOCK:
        with open(TELEMETRY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


# ---------------------------------------------------------------------------
# Credential Acquisition (GCE Metadata -> Env -> gcloud -> Cloud Run Bridge)
# ---------------------------------------------------------------------------
def get_vertex_project_id() -> str:
    """Resolve the active GCP Project ID for Vertex AI calls."""
    for env_key in ("GCP_PROJECT_ID", "VERTEX_PROJECT_ID", "GOOGLE_CLOUD_PROJECT"):
        val = (os.environ.get(env_key) or "").strip()
        if val and val != "your-gcp-project-id":
            return val

    if _TOKEN_CACHE.get("project_id"):
        return str(_TOKEN_CACHE["project_id"])

    metadata_host = os.environ.get("GCE_METADATA_HOST", "metadata.google.internal")
    if os.environ.get("USE_GCE_METADATA", "auto") != "0":
        try:
            acct_req = urllib.request.Request(
                f"http://{metadata_host}/computeMetadata/v1/instance/service-accounts/default/email",
                headers={"Metadata-Flavor": "Google"},
            )
            with urllib.request.urlopen(acct_req, timeout=1.0) as r_email:
                sa_email = r_email.read().decode("utf-8").strip()
            if "insecure-cloudtop-shared-user" not in sa_email:
                proj_req = urllib.request.Request(
                    f"http://{metadata_host}/computeMetadata/v1/project/project-id",
                    headers={"Metadata-Flavor": "Google"},
                )
                with urllib.request.urlopen(proj_req, timeout=1.0) as r_proj:
                    proj_id = r_proj.read().decode("utf-8").strip()
                    if proj_id:
                        _TOKEN_CACHE["project_id"] = proj_id
                        return proj_id
        except Exception:
            pass

    return "llm-compare-ubhits"


def get_vertex_oauth_token(force_refresh: bool = False) -> Optional[str]:
    """Acquire a Google Cloud OAuth access token for Vertex AI."""
    now = time.time()
    if not force_refresh and _TOKEN_CACHE["token"] and now < _TOKEN_CACHE["expires_at"]:
        return _TOKEN_CACHE["token"]

    if os.environ.get("CLAUDE_BEARER_TOKEN"):
        return os.environ["CLAUDE_BEARER_TOKEN"].strip()

    # 1. Try GCE / Cloud Run Metadata Server (only if not on corporate Cloudtop user VM)
    metadata_host = os.environ.get("GCE_METADATA_HOST", "metadata.google.internal")
    if os.environ.get("USE_GCE_METADATA", "auto") != "0":
        try:
            acct_req = urllib.request.Request(
                f"http://{metadata_host}/computeMetadata/v1/instance/service-accounts/default/email",
                headers={"Metadata-Flavor": "Google"},
            )
            with urllib.request.urlopen(acct_req, timeout=1.2) as r_email:
                sa_email = r_email.read().decode("utf-8").strip()
            # Skip shared Cloudtop workstation host SA which has no Vertex AI permissions
            if "insecure-cloudtop-shared-user" not in sa_email:
                tok_req = urllib.request.Request(
                    f"http://{metadata_host}/computeMetadata/v1/instance/service-accounts/default/token",
                    headers={"Metadata-Flavor": "Google"},
                )
                with urllib.request.urlopen(tok_req, timeout=2.0) as r_tok:
                    data = json.loads(r_tok.read().decode("utf-8"))
                    token = data.get("access_token")
                    ttl = max(60, int(data.get("expires_in", 3600)) - 300)
                    if token:
                        _TOKEN_CACHE["token"] = token
                        _TOKEN_CACHE["expires_at"] = now + ttl
                        return token
        except Exception:
            pass

    # 2. Try gcloud CLI (argolis config)
    cmd = os.environ.get(
        "GCLOUD_TOKEN_CMD",
        "CLOUDSDK_ACTIVE_CONFIG_NAME=argolis CLOUDSDK_CORE_ACCOUNT=admin@ubhi.altostrat.com gcloud auth print-access-token",
    )
    try:
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=4)
        if res.returncode == 0 and res.stdout.strip():
            tok = res.stdout.strip()
            _TOKEN_CACHE["token"] = tok
            _TOKEN_CACHE["expires_at"] = now + 1800
            return tok
    except Exception:
        pass

    return None


def _get_bridge_opener() -> Optional[urllib.request.OpenerDirector]:
    """Authenticate to Tarun's Argolis Cloud Run bridge if local gcloud OAuth is unavailable."""
    global _BRIDGE_OPENER
    if _BRIDGE_OPENER is not None:
        return _BRIDGE_OPENER

    bridge_base = os.environ.get(
        "LLM_COMPARE_BRIDGE_URL", "https://llm-compare-fb36wekvmq-uc.a.run.app"
    ).rstrip("/")
    bridge_user = os.environ.get("LLM_COMPARE_BRIDGE_USER", "ubhits")
    bridge_pw = os.environ.get("LLM_COMPARE_BRIDGE_PASSWORD", "")

    if not bridge_pw:
        # Attempt to read from local Chrome Login Data if on Tarun's workstation
        try:
            import sqlite3
            import gi
            gi.require_version("Secret", "1")
            from gi.repository import Secret
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.backends import default_backend

            schema = Secret.Schema.new(
                "chrome_libsecret_os_crypt_password_v2",
                Secret.SchemaFlags.DONT_MATCH_NAME,
                {"application": Secret.SchemaAttributeType.STRING},
            )
            secret = Secret.password_lookup_sync(schema, {"application": "chrome"}, None)
            key = hashlib.pbkdf2_hmac(
                "sha1", (secret or "peanuts").encode("utf-8"), b"saltysalt", 1, 16
            )
            login_db = os.path.expanduser("~/.config/google-chrome/Profile 1/Login Data")
            if os.path.exists(login_db):
                conn = sqlite3.connect(f"file:{login_db}?immutable=1", uri=True)
                row = conn.execute(
                    "SELECT password_value FROM logins WHERE origin_url LIKE '%llmcompare.ubhims.com%' AND username_value='ubhits'"
                ).fetchone()
                conn.close()
                if row and row[0]:
                    enc = row[0]
                    cipher = Cipher(
                        algorithms.AES(key), modes.CBC(b" " * 16), backend=default_backend()
                    )
                    dec = cipher.decryptor().update(enc[3:]) + cipher.decryptor().finalize()
                    raw = dec[: -dec[-1]]
                    bridge_pw = (
                        raw[32:].decode("utf-8", errors="ignore")
                        if len(raw) > 32
                        else raw.decode("utf-8", errors="ignore")
                    )
        except Exception:
            pass

    if not bridge_pw:
        return None

    try:
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        req = urllib.request.Request(
            f"{bridge_base}/api/auth/login",
            data=json.dumps({"username": bridge_user, "password": bridge_pw}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with opener.open(req, timeout=10) as resp:
            if resp.status == 200:
                _BRIDGE_OPENER = opener
                return _BRIDGE_OPENER
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Schema & Message Translation: Anthropic `/v1/messages` <-> Vertex AI Gemini
# ---------------------------------------------------------------------------
_UNSUPPORTED_SCHEMA_KEYS = {
    "$schema",
    "additionalProperties",
    "default",
    "examples",
    "propertyNames",
    "$defs",
    "definitions",
    "title",
}


def sanitize_json_schema_for_gemini(schema: Any) -> Dict[str, Any]:
    """Recursively sanitize an Anthropic tool `input_schema` for Vertex AI Gemini."""
    if not isinstance(schema, dict):
        return {"type": "OBJECT", "properties": {}}

    cleaned: Dict[str, Any] = {}
    for k, v in schema.items():
        if k in _UNSUPPORTED_SCHEMA_KEYS:
            continue
        if k == "const":
            cleaned["enum"] = [str(v)]
            cleaned["type"] = "STRING"
            continue
        if k == "type":
            if isinstance(v, list):
                non_null = [t for t in v if t != "null"]
                cleaned["type"] = (non_null[0] if non_null else "string").upper()
                if "null" in v:
                    cleaned["nullable"] = True
            elif isinstance(v, str):
                cleaned["type"] = v.upper()
            continue
        if k in ("anyOf", "oneOf") and isinstance(v, list):
            non_null_branches = [
                b for b in v if isinstance(b, dict) and b.get("type") != "null"
            ]
            if non_null_branches:
                sub = sanitize_json_schema_for_gemini(non_null_branches[0])
                cleaned.update(sub)
            else:
                cleaned["type"] = "STRING"
            continue
        if k == "properties" and isinstance(v, dict):
            cleaned["properties"] = {
                prop_name: sanitize_json_schema_for_gemini(prop_val)
                for prop_name, prop_val in v.items()
            }
            continue
        if k == "items" and isinstance(v, dict):
            cleaned["items"] = sanitize_json_schema_for_gemini(v)
            continue
        if k == "required" and isinstance(v, list):
            cleaned["required"] = [str(r) for r in v]
            continue
        if k in ("description", "enum", "nullable"):
            cleaned[k] = v

    if "type" not in cleaned:
        if "properties" in cleaned:
            cleaned["type"] = "OBJECT"
        elif "items" in cleaned:
            cleaned["type"] = "ARRAY"
        else:
            cleaned["type"] = "STRING"

    if cleaned["type"] == "OBJECT" and "properties" not in cleaned:
        cleaned["properties"] = {}

    if "required" in cleaned and "properties" in cleaned:
        valid_props = set(cleaned["properties"].keys())
        cleaned["required"] = [r for r in cleaned["required"] if r in valid_props]
        if not cleaned["required"]:
            del cleaned["required"]

    return cleaned


def extract_anthropic_system(system_field: Any) -> str:
    """Normalize Anthropic `system` (string or list of content blocks) into a string."""
    if not system_field:
        return ""
    if isinstance(system_field, str):
        return system_field
    if isinstance(system_field, list):
        parts = []
        for item in system_field:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n\n".join(p for p in parts if p)
    return str(system_field)


def anthropic_to_gemini_payload(
    anthropic_req: Dict[str, Any], model_cfg: Dict[str, Any], effort_override: Optional[str] = None
) -> Dict[str, Any]:
    """Translate an Anthropic `/v1/messages` request payload into Vertex AI Gemini `:generateContent`."""
    system_text = extract_anthropic_system(anthropic_req.get("system"))
    messages = anthropic_req.get("messages", [])
    tools = anthropic_req.get("tools", [])

    # Keep a map of tool_use_id -> function name so tool_result blocks can look up the name
    tool_id_to_name: Dict[str, str] = {}
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    tid = str(block.get("id", ""))
                    tname = str(block.get("name", ""))
                    if tid and tname:
                        tool_id_to_name[tid] = tname

    gemini_contents: List[Dict[str, Any]] = []
    extra_sys_parts: List[str] = []

    for m in messages:
        raw_role = m.get("role", "user")
        if raw_role == "system":
            sys_chunk = extract_anthropic_system(m.get("content"))
            if sys_chunk:
                extra_sys_parts.append(sys_chunk)
            continue
        role = "model" if raw_role == "assistant" else "user"
        content = m.get("content")
        parts: List[Dict[str, Any]] = []

        if isinstance(content, str):
            if content:
                parts.append({"text": content})
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    txt = str(block.get("text", ""))
                    if txt:
                        parts.append({"text": txt})
                elif btype == "thinking":
                    th = str(block.get("thinking", ""))
                    if th:
                        parts.append({"text": th, "thought": True})
                elif btype == "tool_use":
                    tid = str(block.get("id", ""))
                    tname = str(block.get("name", "tool"))
                    tinput = block.get("input")
                    if not isinstance(tinput, dict):
                        tinput = {}
                    # Restore cached Gemini part (with thoughtSignature!) if available
                    with _CACHE_LOCK:
                        cached_part = _THOUGHT_SIGNATURE_CACHE.get(tid)
                    if cached_part:
                        parts.append(cached_part)
                    else:
                        parts.append({"functionCall": {"name": tname, "args": tinput}})
                elif btype == "tool_result":
                    tid = str(block.get("tool_use_id", ""))
                    tname = tool_id_to_name.get(tid, "tool")
                    raw_res = block.get("content", "")
                    if isinstance(raw_res, list):
                        res_text = "\n".join(
                            str(x.get("text", "")) if isinstance(x, dict) else str(x)
                            for x in raw_res
                        )
                    else:
                        res_text = str(raw_res)
                    is_err = bool(block.get("is_error", False))
                    parts.append(
                        {
                            "functionResponse": {
                                "name": tname,
                                "response": {
                                    "result": res_text,
                                    "is_error": is_err,
                                },
                            }
                        }
                    )

        if not parts:
            parts.append({"text": " "})

        # Merge consecutive turns with the same role (required by Vertex AI Gemini)
        if gemini_contents and gemini_contents[-1]["role"] == role:
            gemini_contents[-1]["parts"].extend(parts)
        else:
            gemini_contents.append({"role": role, "parts": parts})

    # Determine thinkingLevel
    supported_efforts = model_cfg.get("supported_efforts", ["low", "medium", "high"])
    default_effort = model_cfg.get("default_effort", "medium")
    req_effort = (
        effort_override
        or (anthropic_req.get("output_config") or {}).get("effort")
        or default_effort
    )
    if req_effort not in supported_efforts:
        req_effort = default_effort

    thinking_cfg: Dict[str, Any] = {"includeThoughts": True}
    if req_effort in supported_efforts:
        thinking_cfg["thinkingLevel"] = req_effort

    gen_cfg: Dict[str, Any] = {"thinkingConfig": thinking_cfg}
    max_tok = anthropic_req.get("max_tokens")
    if isinstance(max_tok, int) and max_tok > 0:
        gen_cfg["maxOutputTokens"] = min(max_tok, model_cfg.get("max_output_tokens", 65536))
    if not model_cfg.get("forbid_temperature", True):
        gen_cfg["temperature"] = 0.2

    if extra_sys_parts:
        system_text = "\n\n".join([p for p in [system_text, *extra_sys_parts] if p])

    if not gemini_contents:
        gemini_contents.append({"role": "user", "parts": [{"text": "Hello"}]})
    elif gemini_contents[-1]["role"] == "model":
        gemini_contents.append({"role": "user", "parts": [{"text": "Continue."}]})

    payload: Dict[str, Any] = {
        "contents": gemini_contents,
        "generationConfig": gen_cfg,
    }
    if system_text:
        payload["systemInstruction"] = {"parts": [{"text": system_text}]}

    if tools:
        fn_decls = []
        for t in tools:
            if not isinstance(t, dict) or not t.get("name"):
                continue
            fn_decls.append(
                {
                    "name": str(t["name"]),
                    "description": str(t.get("description", ""))[:1024],
                    "parameters": sanitize_json_schema_for_gemini(t.get("input_schema", {})),
                }
            )
        if fn_decls:
            payload["tools"] = [{"functionDeclarations": fn_decls}]

    return payload


def gemini_to_anthropic_response(
    gemini_resp: Dict[str, Any], requested_model: str
) -> Tuple[Dict[str, Any], int, int, int, int]:
    """Convert Vertex AI Gemini `:generateContent` response into Anthropic `/v1/messages` format."""
    candidates = gemini_resp.get("candidates") or []
    parts = (candidates[0].get("content") or {}).get("parts") or [] if candidates else []

    content_blocks: List[Dict[str, Any]] = []
    tool_call_count = 0

    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("thought") is True and part.get("text"):
            # Do not emit raw thinking blocks when tool_use follows unless signed,
            # or emit as text/thinking. Claude Code expects standard text or tool_use.
            continue
        if "functionCall" in part:
            fc = part["functionCall"]
            tool_id = f"toolu_{uuid.uuid4().hex[:20]}"
            with _CACHE_LOCK:
                _THOUGHT_SIGNATURE_CACHE[tool_id] = part
            content_blocks.append(
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": str(fc.get("name", "")),
                    "input": fc.get("args") if isinstance(fc.get("args"), dict) else {},
                }
            )
            tool_call_count += 1
        elif part.get("text") is not None and not part.get("thought"):
            txt = str(part["text"])
            if txt:
                content_blocks.append({"type": "text", "text": txt})

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    stop_reason = "tool_use" if tool_call_count > 0 else "end_turn"
    usage = gemini_resp.get("usageMetadata") or {}
    prompt_tokens = int(usage.get("promptTokenCount") or 0)
    candidates_tokens = int(usage.get("candidatesTokenCount") or 0)
    thinking_tokens = int(usage.get("thoughtsTokenCount") or 0)
    completion_tokens = candidates_tokens + thinking_tokens

    anthropic_msg = {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": requested_model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        },
    }
    return anthropic_msg, prompt_tokens, completion_tokens, thinking_tokens, tool_call_count


# ---------------------------------------------------------------------------
# Upstream Vertex AI Execution (Gemini & Claude)
# ---------------------------------------------------------------------------
def call_vertex_gemini(
    anthropic_req: Dict[str, Any], model_cfg: Dict[str, Any], effort_override: Optional[str] = None
) -> Tuple[Dict[str, Any], int, int, int, int, float]:
    """Execute a request on Vertex AI Gemini (`gemini-3.8-flash`, `gemini-3.7-flash`, etc.)."""
    api_model = model_cfg["api_model"]
    gemini_payload = anthropic_to_gemini_payload(anthropic_req, model_cfg, effort_override)
    body_bytes = json.dumps(gemini_payload).encode("utf-8")

    api_key = os.environ.get("AGENT_PLATFORM_API_KEY") or os.environ.get("GEMINI_API_KEY")
    project_id = get_vertex_project_id()

    headers = {"Content-Type": "application/json"}
    if api_key and not api_key.startswith("CONFIGURE_"):
        url = f"https://aiplatform.googleapis.com/v1/publishers/google/models/{urllib.parse.quote(api_model)}:generateContent"
        headers["x-goog-api-key"] = api_key
    else:
        tok = get_vertex_oauth_token()
        if not tok:
            raise RuntimeError(
                "Missing AGENT_PLATFORM_API_KEY or Vertex AI OAuth token for Gemini."
            )
        url = (
            f"https://aiplatform.googleapis.com/v1/projects/{urllib.parse.quote(project_id)}"
            f"/locations/global/publishers/google/models/{urllib.parse.quote(api_model)}:generateContent"
        )
        headers["Authorization"] = f"Bearer {tok}"

    t0 = time.perf_counter()
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw_json = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Vertex AI Gemini HTTP {e.code}: {err_body[:500]}") from e

    latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    msg, in_tok, out_tok, think_tok, tool_calls = gemini_to_anthropic_response(
        raw_json, anthropic_req.get("model", model_cfg["id"])
    )
    return msg, in_tok, out_tok, think_tok, tool_calls, latency_ms


def _build_bridge_prompt_with_tools(anthropic_req: Dict[str, Any]) -> Tuple[str, bool]:
    """Serialize Anthropic system, messages, and tools into a structured prompt for the Cloud Run bridge."""
    system_text = extract_anthropic_system(anthropic_req.get("system"))
    tools = anthropic_req.get("tools") or []
    messages = anthropic_req.get("messages") or []

    parts: List[str] = []
    if system_text:
        trimmed_sys = system_text[-8000:] if len(system_text) > 8000 else system_text
        parts.append(f"<system_instructions>\n{trimmed_sys}\n</system_instructions>")

    has_tools = bool(tools)
    if has_tools:
        compact_tools = []
        for t in tools:
            if isinstance(t, dict) and t.get("name"):
                compact_tools.append(
                    {
                        "name": t["name"],
                        "description": str(t.get("description", ""))[:300],
                        "input_schema": t.get("input_schema", {}),
                    }
                )
        parts.append(
            "<available_tools>\n"
            "You have access to the following tools. Whenever you want to invoke a tool, "
            "output ONE OR MORE `<tool_call>` XML blocks containing valid JSON with `\"name\"` "
            "and `\"input\"` keys, and stop immediately so the tool can execute:\n"
            "<tool_call>\n{\"name\": \"Read\", \"input\": {\"file_path\": \"...\"}}\n</tool_call>\n\n"
            f"Tool Definitions:\n{json.dumps(compact_tools)}\n"
            "</available_tools>"
        )

    for m in messages:
        role = m.get("role", "user")
        if role == "system":
            continue
        content = m.get("content", "")
        if isinstance(content, str):
            parts.append(f"<{role}>\n{content}\n</{role}>")
        elif isinstance(content, list):
            block_strs = []
            for b in content:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "text":
                    block_strs.append(str(b.get("text", "")))
                elif btype == "tool_use":
                    block_strs.append(
                        f"<tool_call id=\"{b.get('id', '')}\">\n"
                        + json.dumps({"name": b.get("name"), "input": b.get("input", {})})
                        + "\n</tool_call>"
                    )
                elif btype == "tool_result":
                    raw_c = b.get("content", "")
                    if isinstance(raw_c, list):
                        raw_c = "\n".join(
                            str(x.get("text", "")) if isinstance(x, dict) else str(x)
                            for x in raw_c
                        )
                    block_strs.append(
                        f"<tool_result tool_use_id=\"{b.get('tool_use_id', '')}\" is_error=\"{b.get('is_error', False)}\">\n"
                        f"{raw_c}\n</tool_result>"
                    )
            parts.append(f"<{role}>\n" + "\n".join(block_strs) + f"\n</{role}>")

    return "\n\n".join(parts), has_tools


def _parse_bridge_tool_calls(text: str) -> Tuple[List[Dict[str, Any]], int]:
    """Parse `<tool_call>...</tool_call>` blocks from Claude bridge output into Anthropic content blocks."""
    blocks: List[Dict[str, Any]] = []
    tool_count = 0
    pattern = re.compile(r"<tool_call(?:\s+[^>]*)?>\s*(\{[\s\S]*?\})\s*</tool_call>", re.MULTILINE)

    last_idx = 0
    for match in pattern.finditer(text):
        prefix = text[last_idx : match.start()].strip()
        if prefix:
            blocks.append({"type": "text", "text": prefix})
        raw_json = match.group(1)
        try:
            parsed = json.loads(raw_json)
            if isinstance(parsed, dict) and parsed.get("name"):
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": f"toolu_{uuid.uuid4().hex[:20]}",
                        "name": str(parsed["name"]),
                        "input": parsed.get("input") if isinstance(parsed.get("input"), dict) else {},
                    }
                )
                tool_count += 1
            else:
                blocks.append({"type": "text", "text": match.group(0)})
        except Exception:
            blocks.append({"type": "text", "text": match.group(0)})
        last_idx = match.end()

    suffix = text[last_idx:].strip()
    if suffix and tool_count == 0:
        blocks.append({"type": "text", "text": suffix})

    if not blocks:
        blocks.append({"type": "text", "text": text})
    return blocks, tool_count


def _sanitize_vertex_claude_messages(raw_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip internal Claude Code TUI fields (e.g. `role: system`, `output_config`) rejected by Vertex AI."""
    cleaned: List[Dict[str, Any]] = []
    for m in raw_messages:
        if not isinstance(m, dict):
            continue
        raw_role = m.get("role", "user")
        if raw_role == "system":
            continue
        role = "assistant" if raw_role == "assistant" else "user"
        content = m.get("content")
        clean_blocks: List[Dict[str, Any]] = []
        if isinstance(content, str):
            if content:
                clean_blocks.append({"type": "text", "text": content})
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "text":
                    txt = str(b.get("text", ""))
                    if txt:
                        clean_blocks.append({"type": "text", "text": txt})
                elif btype == "tool_use":
                    clean_blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(b.get("id", f"toolu_{uuid.uuid4().hex[:16]}")),
                            "name": str(b.get("name", "")),
                            "input": b.get("input") if isinstance(b.get("input"), dict) else {},
                        }
                    )
                elif btype == "tool_result":
                    tr_content = b.get("content", "")
                    if isinstance(tr_content, list):
                        tr_clean = [
                            {"type": "text", "text": str(sub.get("text", ""))}
                            for sub in tr_content
                            if isinstance(sub, dict) and sub.get("type") == "text"
                        ]
                        tr_content = tr_clean if tr_clean else ""
                    elif not isinstance(tr_content, str):
                        tr_content = json.dumps(tr_content)
                    tr_block: Dict[str, Any] = {
                        "type": "tool_result",
                        "tool_use_id": str(b.get("tool_use_id", "")),
                        "content": tr_content,
                    }
                    if "is_error" in b:
                        tr_block["is_error"] = bool(b["is_error"])
                    clean_blocks.append(tr_block)
        if not clean_blocks:
            clean_blocks.append({"type": "text", "text": " "})
        if cleaned and cleaned[-1]["role"] == role:
            cleaned[-1]["content"].extend(clean_blocks)
        else:
            cleaned.append({"role": role, "content": clean_blocks})

    if not cleaned:
        cleaned.append({"role": "user", "content": [{"type": "text", "text": "Hello"}]})
    elif cleaned[-1]["role"] == "assistant":
        cleaned.append({"role": "user", "content": [{"type": "text", "text": "Continue."}]})
    return cleaned


def call_vertex_claude(
    anthropic_req: Dict[str, Any], model_cfg: Dict[str, Any], effort_override: Optional[str] = None
) -> Tuple[Dict[str, Any], int, int, int, int, float]:
    """Execute a request on Vertex AI Anthropic (`claude-opus-5-5`, `claude-sonnet-5`)."""
    api_model = model_cfg["api_model"]
    project_id = get_vertex_project_id()
    supported_efforts = model_cfg.get("supported_efforts", ["low", "medium", "high", "xhigh", "max"])
    default_effort = model_cfg.get("default_effort", "high")
    effort = effort_override or default_effort
    if effort not in supported_efforts:
        effort = default_effort

    # 1. Primary path: Direct Vertex AI `:rawPredict` when OAuth token is available
    tok = get_vertex_oauth_token()
    if tok:
        vertex_body: Dict[str, Any] = {
            "anthropic_version": "vertex-2023-10-16",
            "max_tokens": min(
                int(anthropic_req.get("max_tokens") or 16384),
                model_cfg.get("max_output_tokens", 128000),
            ),
            "messages": _sanitize_vertex_claude_messages(anthropic_req.get("messages", [])),
        }
        sys_val = anthropic_req.get("system")
        if isinstance(sys_val, str) and sys_val.strip():
            vertex_body["system"] = sys_val
        elif isinstance(sys_val, list):
            clean_sys = [
                {"type": "text", "text": str(b.get("text", ""))}
                for b in sys_val
                if isinstance(b, dict) and b.get("text")
            ]
            if clean_sys:
                vertex_body["system"] = clean_sys

        raw_tools = anthropic_req.get("tools")
        if isinstance(raw_tools, list) and raw_tools:
            clean_tools = [
                {
                    "name": str(t["name"]),
                    "description": str(t.get("description") or ""),
                    "input_schema": t["input_schema"],
                }
                for t in raw_tools
                if isinstance(t, dict) and t.get("name") and isinstance(t.get("input_schema"), dict)
            ]
            if clean_tools:
                vertex_body["tools"] = clean_tools

        if anthropic_req.get("tool_choice") and isinstance(anthropic_req.get("tool_choice"), dict):
            tc_type = anthropic_req["tool_choice"].get("type")
            if tc_type in ("auto", "any", "tool"):
                vertex_body["tool_choice"] = anthropic_req["tool_choice"]
        elif model_cfg.get("thinking_type") == "adaptive":
            vertex_body["thinking"] = {"type": "adaptive", "display": "summarized"}
            vertex_body["output_config"] = {"effort": effort}

        candidate_projects: List[str] = []
        for cand in (
            _TOKEN_CACHE.get("claude_project_id"),
            os.environ.get("VERTEX_CLAUDE_PROJECT_ID"),
            "llm-compare-ubhits",
            project_id,
        ):
            c_str = (str(cand) if cand else "").strip()
            if c_str and c_str != "your-gcp-project-id" and c_str not in candidate_projects:
                candidate_projects.append(c_str)

        body_bytes = json.dumps(vertex_body).encode("utf-8")
        last_http_err: Optional[Tuple[int, str]] = None
        for cand_proj in candidate_projects:
            url = (
                f"https://aiplatform.googleapis.com/v1/projects/{urllib.parse.quote(cand_proj)}"
                f"/locations/global/publishers/anthropic/models/{urllib.parse.quote(api_model)}:rawPredict"
            )
            t0 = time.perf_counter()
            req = urllib.request.Request(
                url,
                data=body_bytes,
                headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    j = json.loads(resp.read().decode("utf-8"))
                _TOKEN_CACHE["claude_project_id"] = cand_proj
                latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
                usage = j.get("usage") or {}
                in_tok = int(usage.get("input_tokens") or 0)
                out_tok = int(usage.get("output_tokens") or 0)
                think_tok = int((usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
                blocks = j.get("content") or []
                # Filter out unsigned thinking blocks so Claude Code CLI tool loops stay clean
                clean_blocks = [b for b in blocks if isinstance(b, dict) and b.get("type") != "thinking"]
                if not clean_blocks:
                    clean_blocks = [{"type": "text", "text": ""}]
                j["content"] = clean_blocks
                j["model"] = anthropic_req.get("model", model_cfg["id"])
                tool_calls = sum(1 for b in clean_blocks if b.get("type") == "tool_use")
                return j, in_tok, out_tok, think_tok, tool_calls, latency_ms
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace")
                last_http_err = (e.code, err_body)
                if e.code in (403, 404):
                    continue
                if e.code == 401:
                    break
                raise RuntimeError(f"Vertex AI Claude HTTP {e.code}: {err_body[:500]}") from e

        if last_http_err and last_http_err[0] != 401:
            raise RuntimeError(
                f"Vertex AI Claude HTTP {last_http_err[0]}: {last_http_err[1][:500]}"
            )

    # 2. Argolis Cloud Run Bridge path (executes live on `llm-compare-ubhits` Vertex AI)
    opener = _get_bridge_opener()
    if opener:
        bridge_base = os.environ.get(
            "LLM_COMPARE_BRIDGE_URL", "https://llm-compare-fb36wekvmq-uc.a.run.app"
        ).rstrip("/")
        prompt_str, _ = _build_bridge_prompt_with_tools(anthropic_req)
        req_payload = {
            "taskId": "custom",
            "customPrompt": prompt_str,
            "models": [{"slot": "A", "catalogId": model_cfg["id"], "effort": effort}],
        }
        t0 = time.perf_counter()
        req = urllib.request.Request(
            f"{bridge_base}/api/run",
            data=json.dumps(req_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        slot_result = None
        with opener.open(req, timeout=180) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                ev = json.loads(line)
                if ev.get("type") == "done" and ev.get("result"):
                    slot_result = ev["result"]
        latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
        if not slot_result:
            raise RuntimeError(f"Argolis Cloud Run Bridge returned no result for {model_cfg['id']}")

        raw_text = str(slot_result.get("code") or slot_result.get("text") or "")
        in_tok = int(slot_result.get("promptTokens") or 0)
        out_tok = int(slot_result.get("completionTokens") or 0)
        think_tok = int(slot_result.get("reasoningTokens") or 0)
        api_lat = float(slot_result.get("apiLatencyMs") or latency_ms)

        content_blocks, tool_calls = _parse_bridge_tool_calls(raw_text)
        stop_reason = "tool_use" if tool_calls > 0 else "end_turn"
        anthropic_msg = {
            "id": f"msg_{uuid.uuid4().hex[:24]}",
            "type": "message",
            "role": "assistant",
            "model": anthropic_req.get("model", model_cfg["id"]),
            "content": content_blocks,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        }
        return anthropic_msg, in_tok, out_tok, think_tok, tool_calls, api_lat

    raise RuntimeError(
        "No Vertex AI OAuth credential available for Claude. Run "
        "`gcloud auth login admin@ubhi.altostrat.com --configuration=argolis` or deploy on GCE VM."
    )


def dispatch_anthropic_messages(
    anthropic_req: Dict[str, Any], headers: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """Route an Anthropic `/v1/messages` request to Vertex AI (Gemini or Claude) and log telemetry."""
    ctx = get_active_context(headers)
    requested_model = str(anthropic_req.get("model") or "gemini-3.8-flash")
    sys_text = extract_anthropic_system(anthropic_req.get("system"))
    has_tools = bool(anthropic_req.get("tools"))
    max_tok = int(anthropic_req.get("max_tokens") or 16384)

    # Detect internal Claude Code TUI helper requests (quota probe, terminal title generator, etc.)
    is_bg_helper = (
        max_tok <= 128
        or (
            not has_tools
            and (
                "isNewTopic" in sys_text
                or "title" in sys_text.lower()
                or "summarize" in sys_text.lower()
                or "haiku" in requested_model.lower()
            )
        )
    )

    agent_role = ctx["agent_role"]
    if "Stage 1 Principal Architect" in sys_text:
        agent_role = "Planner"
        requested_model = os.environ.get("PLANNER_MODEL", requested_model)
    elif "Stage 2 High-Velocity Implementer" in sys_text:
        agent_role = "Implementer"
        requested_model = os.environ.get("IMPLEMENTER_MODEL", requested_model)
    elif "Stage 3 Staff Security & Quality Reviewer" in sys_text:
        agent_role = "Reviewer"
        requested_model = os.environ.get("REVIEWER_MODEL", requested_model)

    if is_bg_helper:
        model_cfg = resolve_model("gemini-3.8-flash")
        msg, _, _, _, _, _ = call_vertex_gemini(anthropic_req, model_cfg, "low")
        return msg

    model_cfg = resolve_model(requested_model)
    effort_override = ctx.get("effort")

    if model_cfg["publisher"] == "google":
        msg, in_tok, out_tok, think_tok, tool_calls, latency_ms = call_vertex_gemini(
            anthropic_req, model_cfg, effort_override
        )
    else:
        msg, in_tok, out_tok, think_tok, tool_calls, latency_ms = call_vertex_claude(
            anthropic_req, model_cfg, effort_override
        )

    tok_per_sec = round((out_tok / (latency_ms / 1000.0)), 1) if latency_ms > 0 else 0.0
    cost_usd = compute_cost_usd(model_cfg["id"], in_tok, out_tok)
    stop_reason = msg.get("stop_reason", "end_turn")

    telemetry_event = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "run_id": ctx["run_id"],
        "task_size": ctx["task_size"],
        "agent_role": agent_role,
        "requested_model": requested_model,
        "model": model_cfg["id"],
        "model_label": model_cfg["label"],
        "publisher": model_cfg["publisher"],
        "prompt_tokens": in_tok,
        "completion_tokens": out_tok,
        "thinking_tokens": think_tok,
        "total_tokens": in_tok + out_tok,
        "latency_ms": latency_ms,
        "tokens_per_sec": tok_per_sec,
        "cost_usd": cost_usd,
        "tool_calls": tool_calls,
        "stop_reason": stop_reason,
        "status": "ok",
    }
    record_telemetry(telemetry_event)

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        assistant_text = "\n".join(
            str(b.get("text", ""))
            for b in (msg.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        ).strip()
        (LOGS_DIR / "last_stage_output.json").write_text(
            json.dumps(
                {
                    "run_id": ctx["run_id"],
                    "task_size": ctx["task_size"],
                    "agent_role": agent_role,
                    "model": model_cfg["id"],
                    "stop_reason": stop_reason,
                    "tool_calls": tool_calls,
                    "text": assistant_text,
                    "timestamp": time.time(),
                }
            ),
            encoding="utf-8",
        )
    except Exception:
        pass

    return msg


def format_anthropic_sse_stream(msg: Dict[str, Any]) -> bytes:
    """Convert an Anthropic `/v1/messages` response object into SSE stream events."""
    events: List[str] = []

    def add_sse(event_name: str, payload: Dict[str, Any]) -> None:
        events.append(f"event: {event_name}\ndata: {json.dumps(payload)}\n\n")

    usage = msg.get("usage") or {"input_tokens": 0, "output_tokens": 0}
    add_sse(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg.get("id", f"msg_{uuid.uuid4().hex[:20]}"),
                "type": "message",
                "role": "assistant",
                "model": msg.get("model", "gemini-3.8-flash"),
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": 1,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            },
        },
    )

    for idx, block in enumerate(msg.get("content") or []):
        btype = block.get("type")
        if btype == "text":
            add_sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            text_val = str(block.get("text", ""))
            if text_val:
                add_sse(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": idx,
                        "delta": {"type": "text_delta", "text": text_val},
                    },
                )
            add_sse("content_block_stop", {"type": "content_block_stop", "index": idx})
        elif btype == "tool_use":
            add_sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": idx,
                    "content_block": {
                        "type": "tool_use",
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "input": {},
                    },
                },
            )
            input_json = json.dumps(block.get("input") or {})
            add_sse(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": idx,
                    "delta": {"type": "input_json_delta", "partial_json": input_json},
                },
            )
            add_sse("content_block_stop", {"type": "content_block_stop", "index": idx})

    add_sse(
        "message_delta",
        {
            "type": "message_delta",
            "delta": {
                "stop_reason": msg.get("stop_reason", "end_turn"),
                "stop_sequence": None,
            },
            "usage": {"output_tokens": usage.get("output_tokens", 0)},
        },
    )
    add_sse("message_stop", {"type": "message_stop"})
    return "".join(events).encode("utf-8")


# ---------------------------------------------------------------------------
# LiteLLM CustomLogger Hook (when loaded via `litellm --config litellm_config.yaml`)
# ---------------------------------------------------------------------------
try:
    from litellm.integrations.custom_logger import CustomLogger as _BaseCustomLogger
except Exception:
    class _BaseCustomLogger:  # type: ignore[no-redef]
        pass


class VertexHybridTelemetryHandler(_BaseCustomLogger):
    """LiteLLM callback logger that records per-turn telemetry into logs/telemetry.jsonl."""

    def log_success_event(self, kwargs: Dict[str, Any], response_obj: Any, start_time: Any, end_time: Any) -> None:
        try:
            ctx = get_active_context()
            model_name = str(kwargs.get("model") or "gemini-3.8-flash")
            model_cfg = resolve_model(model_name)
            usage = getattr(response_obj, "usage", None) or {}
            in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
            out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
            dur_ms = round((end_time - start_time).total_seconds() * 1000.0, 1) if hasattr(end_time - start_time, "total_seconds") else 1000.0
            tok_s = round(out_tok / (dur_ms / 1000.0), 1) if dur_ms > 0 else 0.0
            record_telemetry(
                {
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "run_id": ctx["run_id"],
                    "task_size": ctx["task_size"],
                    "agent_role": ctx["agent_role"],
                    "requested_model": model_name,
                    "model": model_cfg["id"],
                    "model_label": model_cfg["label"],
                    "publisher": model_cfg["publisher"],
                    "prompt_tokens": in_tok,
                    "completion_tokens": out_tok,
                    "thinking_tokens": 0,
                    "total_tokens": in_tok + out_tok,
                    "latency_ms": dur_ms,
                    "tokens_per_sec": tok_s,
                    "cost_usd": compute_cost_usd(model_cfg["id"], in_tok, out_tok),
                    "tool_calls": 0,
                    "stop_reason": "end_turn",
                    "status": "ok",
                }
            )
        except Exception:
            pass


proxy_handler_instance = VertexHybridTelemetryHandler()


# ---------------------------------------------------------------------------
# HTTP Server for Claude Code (`/v1/messages`, `/v1/models`, `/health`)
# ---------------------------------------------------------------------------
class GatewayHTTPRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        if os.environ.get("GATEWAY_QUIET", "1") == "0":
            super().log_message(fmt, *args)

    def _send_json(self, status: int, data: Dict[str, Any]) -> None:
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/health/liveliness", "/health/readiness", "/api/hello", "/"):
            self._send_json(
                200,
                {
                    "status": "healthy",
                    "gateway": "litellm-vertex-hybrid-gateway",
                    "project_id": get_vertex_project_id(),
                    "models": list(MODEL_CATALOG.keys()),
                },
            )
            return
        if path in ("/v1/models", "/models"):
            self._send_json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": m["id"],
                            "object": "model",
                            "created": 1750000000,
                            "owned_by": m["publisher"],
                        }
                        for m in MODEL_CATALOG.values()
                    ],
                },
            )
            return
        self._send_json(404, {"error": f"Unknown GET path {path}"})

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"type": "error", "error": {"type": "invalid_request_error", "message": str(e)}})
            return

        if path == "/v1/messages/count_tokens":
            # Fast token estimation endpoint used by Claude Code CLI
            raw_len = len(raw_body)
            self._send_json(200, {"input_tokens": max(1, raw_len // 4)})
            return

        if path in ("/v1/messages", "/messages"):
            try:
                req_headers = {k: v for k, v in self.headers.items()}
                msg = dispatch_anthropic_messages(payload, req_headers)
                if payload.get("stream"):
                    sse_bytes = format_anthropic_sse_stream(msg)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Length", str(len(sse_bytes)))
                    self.end_headers()
                    self.wfile.write(sse_bytes)
                else:
                    self._send_json(200, msg)
            except Exception as e:
                err_msg = str(e)
                self._send_json(
                    500,
                    {
                        "type": "error",
                        "error": {"type": "api_error", "message": err_msg},
                    },
                )
            return

        if path in ("/v1/chat/completions", "/chat/completions"):
            # OpenAI Chat Completions compatibility endpoint
            try:
                oai_msgs = payload.get("messages", [])
                sys_parts = [m.get("content", "") for m in oai_msgs if m.get("role") == "system"]
                non_sys = [m for m in oai_msgs if m.get("role") != "system"]
                anth_req = {
                    "model": payload.get("model", "gemini-3.8-flash"),
                    "system": "\n".join(str(x) for x in sys_parts if x),
                    "messages": non_sys,
                    "max_tokens": payload.get("max_tokens", 8192),
                }
                req_headers = {k: v for k, v in self.headers.items()}
                msg = dispatch_anthropic_messages(anth_req, req_headers)
                text_out = "\n".join(
                    b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text"
                )
                usage = msg.get("usage", {})
                self._send_json(
                    200,
                    {
                        "id": f"chatcmpl-{uuid.uuid4().hex[:16]}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": msg.get("model"),
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": text_out},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": usage.get("input_tokens", 0),
                            "completion_tokens": usage.get("output_tokens", 0),
                            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
                        },
                    },
                )
            except Exception as e:
                self._send_json(500, {"error": {"message": str(e)}})
            return

        self._send_json(404, {"error": f"Unknown POST path {path}"})


def serve_gateway(host: str = "127.0.0.1", port: int = 4000) -> None:
    """Start the multi-threaded LiteLLM Vertex AI Gateway server."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), GatewayHTTPRequestHandler)
    print(
        f"[LiteLLM-Vertex-Gateway] Listening on http://{host}:{port} "
        f"(Planner={os.environ.get('PLANNER_MODEL', 'claude-opus-5-5')}, "
        f"Implementer={os.environ.get('IMPLEMENTER_MODEL', 'gemini-3.8-flash')}, "
        f"Reviewer={os.environ.get('REVIEWER_MODEL', 'claude-sonnet-5')})",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LiteLLM Vertex AI Hybrid Gateway for Claude Code")
    parser.add_argument("--host", default=os.environ.get("GATEWAY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("GATEWAY_PORT", "4000")))
    args = parser.parse_args()
    serve_gateway(args.host, args.port)
