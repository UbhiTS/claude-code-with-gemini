"""Authoritative Vertex AI Model Garden catalog, pricing, and thinking rules.

Synchronized with Tarun's verified Argolis benchmark catalog (`llm-compare/src/pricing.js`
and `llm-compare/src/providers.js`). All prices are USD per 1,000,000 tokens.
"""

from typing import Dict, Any, Optional

MODEL_CATALOG: Dict[str, Dict[str, Any]] = {
    # --- Google Gemini on Vertex AI Agent Platform ---
    "gemini-3.8-flash": {
        "id": "gemini-3.8-flash",
        "label": "Gemini 3.8 Flash",
        "publisher": "google",
        "api_model": "gemini-3.8-flash",
        "input_price_per_1m": 0.75,
        "output_price_per_1m": 3.75,
        "context_window": 1_000_000,
        "max_output_tokens": 65_536,
        # Gemini 3.8 Flash supports low | medium | high; rejects 'minimal' & custom temperature
        "supported_efforts": ["low", "medium", "high"],
        "default_effort": "medium",
        "forbid_temperature": True,
        "fallback_tok_per_sec": 138.0,
    },
    "gemini-3.7-flash": {
        "id": "gemini-3.7-flash",
        "label": "Gemini 3.7 Flash",
        "publisher": "google",
        "api_model": "gemini-3.7-flash",
        "input_price_per_1m": 0.75,
        "output_price_per_1m": 3.75,
        "context_window": 1_000_000,
        "max_output_tokens": 65_536,
        "supported_efforts": ["low", "medium", "high"],
        "default_effort": "medium",
        "forbid_temperature": True,
        "fallback_tok_per_sec": 132.0,
    },
    "gemini-3.5-flash": {
        "id": "gemini-3.5-flash",
        "label": "Gemini 3.5 Flash",
        "publisher": "google",
        "api_model": "gemini-3.5-flash",
        "input_price_per_1m": 1.50,
        "output_price_per_1m": 9.00,
        "context_window": 1_000_000,
        "max_output_tokens": 65_536,
        "supported_efforts": ["minimal", "low", "medium", "high"],
        "default_effort": "medium",
        "forbid_temperature": False,
        "fallback_tok_per_sec": 145.0,
    },
    "gemini-3.5-flash-lite": {
        "id": "gemini-3.5-flash-lite",
        "label": "Gemini 3.5 Flash-Lite",
        "publisher": "google",
        "api_model": "gemini-3.5-flash-lite",
        "input_price_per_1m": 0.30,
        "output_price_per_1m": 2.50,
        "context_window": 1_000_000,
        "max_output_tokens": 65_536,
        "supported_efforts": ["minimal", "low", "medium", "high"],
        "default_effort": "minimal",
        "forbid_temperature": False,
        "fallback_tok_per_sec": 185.0,
    },
    "gemini-3.1-pro": {
        "id": "gemini-3.1-pro",
        "label": "Gemini 3.1 Pro",
        "publisher": "google",
        "api_model": "gemini-3.1-pro-preview",
        "input_price_per_1m": 2.00,
        "output_price_per_1m": 12.00,
        "context_window": 200_000,
        "max_output_tokens": 65_536,
        "supported_efforts": ["low", "medium", "high"],
        "default_effort": "high",
        "forbid_temperature": True,
        "fallback_tok_per_sec": 74.0,
    },
    # --- Anthropic Claude on Vertex AI Partner Endpoint ---
    "claude-opus-5-5": {
        "id": "claude-opus-5-5",
        "label": "Claude Opus 5.5",
        "publisher": "anthropic",
        "api_model": "claude-opus-5-5",
        "input_price_per_1m": 5.00,
        "output_price_per_1m": 25.00,
        "context_window": 1_000_000,
        "max_output_tokens": 128_000,
        "supported_efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "high",
        "thinking_type": "adaptive",
        "fallback_tok_per_sec": 52.0,
    },
    "claude-opus-5": {
        "id": "claude-opus-5",
        "label": "Claude Opus 5",
        "publisher": "anthropic",
        "api_model": "claude-opus-5",
        "input_price_per_1m": 5.00,
        "output_price_per_1m": 25.00,
        "context_window": 1_000_000,
        "max_output_tokens": 128_000,
        "supported_efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "high",
        "thinking_type": "adaptive",
        "fallback_tok_per_sec": 54.0,
    },
    "claude-sonnet-5": {
        "id": "claude-sonnet-5",
        "label": "Claude Sonnet 5",
        "publisher": "anthropic",
        "api_model": "claude-sonnet-5",
        "input_price_per_1m": 3.00,
        "output_price_per_1m": 15.00,
        "context_window": 1_000_000,
        "max_output_tokens": 128_000,
        "supported_efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "medium",
        "thinking_type": "adaptive",
        "fallback_tok_per_sec": 72.0,
    },
    "claude-opus-4-8": {
        "id": "claude-opus-4-8",
        "label": "Claude Opus 4.8",
        "publisher": "anthropic",
        "api_model": "claude-opus-4-8",
        "input_price_per_1m": 5.00,
        "output_price_per_1m": 25.00,
        "context_window": 1_000_000,
        "max_output_tokens": 128_000,
        "supported_efforts": ["low", "medium", "high", "xhigh", "max"],
        "default_effort": "high",
        "thinking_type": "adaptive",
        "fallback_tok_per_sec": 50.0,
    },
    "claude-sonnet-4-6": {
        "id": "claude-sonnet-4-6",
        "label": "Claude Sonnet 4.6",
        "publisher": "anthropic",
        "api_model": "claude-sonnet-4-6",
        "input_price_per_1m": 3.00,
        "output_price_per_1m": 15.00,
        "context_window": 1_000_000,
        "max_output_tokens": 128_000,
        "supported_efforts": ["low", "medium", "high", "max"],
        "default_effort": "medium",
        "thinking_type": "adaptive",
        "fallback_tok_per_sec": 68.0,
    },
    "claude-haiku-4-5": {
        "id": "claude-haiku-4-5",
        "label": "Claude Haiku 4.5",
        "publisher": "anthropic",
        "api_model": "claude-haiku-4-5",
        "input_price_per_1m": 1.00,
        "output_price_per_1m": 5.00,
        "context_window": 200_000,
        "max_output_tokens": 32_000,
        "supported_efforts": [],
        "default_effort": "",
        "thinking_type": "enabled",
        "fallback_tok_per_sec": 110.0,
    },
}

MODEL_ALIASES: Dict[str, str] = {
    "gemini-3.1-pro-preview": "gemini-3.1-pro",
    "vertex_ai/gemini-3.8-flash": "gemini-3.8-flash",
    "vertex_ai/gemini-3.7-flash": "gemini-3.7-flash",
    "vertex_ai/gemini-3.5-flash": "gemini-3.5-flash",
    "vertex_ai/gemini-3.5-flash-lite": "gemini-3.5-flash-lite",
    "vertex_ai/gemini-3.1-pro": "gemini-3.1-pro",
    "vertex_ai/gemini-3.1-pro-preview": "gemini-3.1-pro",
    "vertex_ai/claude-opus-5-5": "claude-opus-5-5",
    "vertex_ai/claude-opus-5": "claude-opus-5",
    "vertex_ai/claude-sonnet-5": "claude-sonnet-5",
    "vertex_ai/claude-opus-4-8": "claude-opus-4-8",
    "vertex_ai/claude-sonnet-4-6": "claude-sonnet-4-6",
    "vertex_ai/claude-haiku-4-5": "claude-haiku-4-5",
}


def resolve_model(model_id: str) -> Dict[str, Any]:
    """Return the canonical catalog entry for a model ID or alias."""
    raw = (model_id or "").strip()
    if raw.startswith("anthropic/") or raw.startswith("google/"):
        raw = raw.split("/", 1)[1]
    canonical = MODEL_ALIASES.get(raw, raw)
    if canonical in MODEL_CATALOG:
        return MODEL_CATALOG[canonical]
    # Sensible fallback if a user passes an experimental Gemini or Claude ID
    if "gemini" in canonical.lower():
        return {
            **MODEL_CATALOG["gemini-3.8-flash"],
            "id": canonical,
            "label": canonical,
            "api_model": canonical,
        }
    return {
        **MODEL_CATALOG["claude-sonnet-5"],
        "id": canonical,
        "label": canonical,
        "api_model": canonical,
    }


def compute_cost_usd(model_id: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Compute exact USD cost from prompt and completion tokens (completion includes thinking)."""
    entry = resolve_model(model_id)
    in_cost = (max(0, prompt_tokens) / 1_000_000.0) * float(entry["input_price_per_1m"])
    out_cost = (max(0, completion_tokens) / 1_000_000.0) * float(entry["output_price_per_1m"])
    return round(in_cost + out_cost, 6)


def normalize_gemini_effort(model_id: str, effort: Optional[str]) -> str:
    """Normalize thinkingLevel effort so models like gemini-3.8-flash never receive unsupported 'minimal'."""
    entry = resolve_model(model_id)
    supported = entry.get("supported_efforts") or ["low", "medium", "high"]
    candidate = (effort or entry.get("default_effort") or "medium").strip().lower()
    if candidate in supported:
        return candidate
    if candidate == "minimal" and "low" in supported:
        return "low"
    return str(entry.get("default_effort") or "medium")

