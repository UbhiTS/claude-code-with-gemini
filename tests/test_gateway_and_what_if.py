"""Unit tests for LiteLLM Vertex AI Gateway translation, thoughtSignature cache, and What-If Engine."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.catalog import MODEL_CATALOG, compute_cost_usd, normalize_gemini_effort, resolve_model
from src.litellm_vertex_gateway import (
    anthropic_to_gemini_payload,
    format_anthropic_sse_stream,
    gemini_to_anthropic_response,
    get_vertex_project_id,
)
from src.what_if_engine import build_what_if_analysis, render_terminal_report


def test_get_vertex_project_id_env_precedence(monkeypatch) -> None:
    monkeypatch.setenv("USE_GCE_METADATA", "0")
    monkeypatch.delenv("GCP_PROJECT_ID", raising=False)
    monkeypatch.delenv("VERTEX_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

    # Fallback when unset
    assert get_vertex_project_id() == "llm-compare-ubhits"

    # VERTEX_PROJECT_ID set by vm_startup.sh
    monkeypatch.setenv("VERTEX_PROJECT_ID", "demo-vertex-proj-123")
    assert get_vertex_project_id() == "demo-vertex-proj-123"

    # GCP_PROJECT_ID takes precedence
    monkeypatch.setenv("GCP_PROJECT_ID", "custom-gcp-proj-456")
    assert get_vertex_project_id() == "custom-gcp-proj-456"


def test_catalog_pricing_and_gemini_38_rules() -> None:
    opus = resolve_model("claude-opus-5-5")
    assert opus["input_price_per_1m"] == 5.00
    assert opus["output_price_per_1m"] == 25.00

    flash = resolve_model("gemini-3.8-flash")
    assert flash["input_price_per_1m"] == 0.75
    assert flash["output_price_per_1m"] == 3.75
    assert flash["forbid_temperature"] is True
    # Gemini 3.8 Flash rejects 'minimal' -> must normalize to 'low'
    assert normalize_gemini_effort("gemini-3.8-flash", "minimal") == "low"
    assert normalize_gemini_effort("gemini-3.8-flash", "high") == "high"

    # 1M input + 1M output on Gemini 3.8 Flash = $0.75 + $3.75 = $4.50
    assert compute_cost_usd("gemini-3.8-flash", 1_000_000, 1_000_000) == 4.50
    # 1M input + 1M output on Claude Opus 5.5 = $5.00 + $25.00 = $30.00
    assert compute_cost_usd("claude-opus-5-5", 1_000_000, 1_000_000) == 30.00


def test_anthropic_to_gemini_tool_and_thought_signature_roundtrip() -> None:
    # Simulate Gemini 3.8 Flash returning a functionCall with thoughtSignature
    raw_gemini_resp = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "name": "Read",
                                "args": {"file_path": "rate_limiter.py"},
                            },
                            "thoughtSignature": "sig-xyz-987",
                        }
                    ],
                },
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 1200,
            "candidatesTokenCount": 45,
            "thoughtsTokenCount": 80,
        },
    }
    anth_msg, in_tok, out_tok, think_tok, tool_calls = gemini_to_anthropic_response(
        raw_gemini_resp, "gemini-3.8-flash"
    )
    assert anth_msg["stop_reason"] == "tool_use"
    assert tool_calls == 1
    assert in_tok == 1200
    assert think_tok == 80
    tool_block = anth_msg["content"][0]
    assert tool_block["type"] == "tool_use"
    assert tool_block["name"] == "Read"
    tool_id = tool_block["id"]

    # Now construct the follow-up Anthropic request sending tool_result for tool_id
    follow_up_req = {
        "model": "gemini-3.8-flash",
        "system": "You are a coding assistant.",
        "temperature": 0.2,  # Must be stripped for gemini-3.8-flash!
        "tools": [
            {
                "name": "Read",
                "description": "Read file",
                "input_schema": {"type": "object", "properties": {"file_path": {"type": "string"}}},
            }
        ],
        "messages": [
            {"role": "user", "content": "Read rate_limiter.py"},
            {"role": "assistant", "content": [tool_block]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": "class TokenBucket: pass",
                    }
                ],
            },
        ],
    }
    gemini_payload = anthropic_to_gemini_payload(
        follow_up_req, resolve_model("gemini-3.8-flash"), effort_override="medium"
    )
    # Verify custom temperature was omitted for Gemini 3.8 Flash
    assert "temperature" not in gemini_payload["generationConfig"]
    assert gemini_payload["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "medium"

    # Verify the cached thoughtSignature was re-attached to the model functionCall part
    model_turn = gemini_payload["contents"][1]
    assert model_turn["role"] == "model"
    assert model_turn["parts"][0].get("thoughtSignature") == "sig-xyz-987"

    # Verify SSE stream formatting works cleanly
    sse_bytes = format_anthropic_sse_stream(anth_msg)
    assert b"event: message_start" in sse_bytes
    assert b"event: content_block_start" in sse_bytes
    assert b"event: message_stop" in sse_bytes


def test_what_if_counterfactual_engine() -> None:
    sample_rows = [
        {
            "run_id": "unit-run-1",
            "task_size": "small",
            "agent_role": "Planner",
            "model": "claude-opus-5-5",
            "prompt_tokens": 6000,
            "completion_tokens": 1200,
            "thinking_tokens": 400,
            "latency_ms": 12000.0,
            "cost_usd": compute_cost_usd("claude-opus-5-5", 6000, 1200),
            "tool_calls": 0,
            "status": "ok",
        },
        {
            "run_id": "unit-run-1",
            "task_size": "small",
            "agent_role": "Implementer",
            "model": "gemini-3.8-flash",
            "prompt_tokens": 85000,
            "completion_tokens": 3500,
            "thinking_tokens": 900,
            "latency_ms": 58000.0,
            "cost_usd": compute_cost_usd("gemini-3.8-flash", 85000, 3500),
            "tool_calls": 10,
            "status": "ok",
        },
        {
            "run_id": "unit-run-1",
            "task_size": "small",
            "agent_role": "Reviewer",
            "model": "claude-sonnet-5",
            "prompt_tokens": 4000,
            "completion_tokens": 500,
            "thinking_tokens": 150,
            "latency_ms": 6000.0,
            "cost_usd": compute_cost_usd("claude-sonnet-5", 4000, 500),
            "tool_calls": 0,
            "status": "ok",
        },
    ]
    report = build_what_if_analysis(sample_rows, run_id="unit-run-1", task_size="small")
    assert report["totals"]["turns"] == 3
    assert report["totals"]["tool_calls"] == 10
    sc_hybrid = report["scenarios"][0]
    sc_opus = report["scenarios"][1]
    assert sc_hybrid["total_cost_usd"] < sc_opus["total_cost_usd"]
    assert sc_hybrid["savings_vs_opus_pct"] > 70.0
    text_out = render_terminal_report(report)
    assert "COUNTERFACTUAL WHAT-IF MATRIX" in text_out
