"""
Helios terminal UI — a fast, agent-centric REPL over any configured gateway.

Two modes, always visibly labeled:

* ``GOVERNED`` — the default ``helios`` gateway: prompts go through
  ``POST /v1/ai/complete``, so every turn produces a DecisionTrace and passes
  policy, routing, and evaluation.
* ``DIRECT``   — any OpenAI-compatible gateway (hosted or local) via the
  standard Chat Completions contract.  Helios governance is bypassed; the
  status line says so.

Pure helpers (payload builders / response extractors) live here so the test
suite can exercise them without a network or a live terminal.
"""

from __future__ import annotations

from typing import Any

from helios.gateways import GatewayProfile


def build_governed_payload(prompt: str, model: str | None = None) -> dict[str, Any]:
    """Request body for the governed Helios path (`POST /v1/ai/complete`)."""
    payload: dict[str, Any] = {"input": prompt}
    if model:
        payload["model"] = model
    return payload


def build_direct_payload(
    prompt: str,
    model: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Standard OpenAI Chat Completions request body."""
    messages = list(history or [])
    messages.append({"role": "user", "content": prompt})
    return {"model": model, "messages": messages}


def extract_governed_output(data: dict[str, Any]) -> dict[str, Any]:
    """Pull the display fields out of a `/v1/ai/complete` response."""
    return {
        "output": data.get("output", ""),
        "trace_id": data.get("trace_id"),
        "model": data.get("model", {}),
        "cost_usd": data.get("cost_usd"),
        "latency_ms": data.get("latency_ms"),
        "citations": data.get("citations", []),
    }


def extract_direct_output(data: dict[str, Any]) -> str:
    """Pull the assistant text out of a Chat Completions response."""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content") or ""


def completion_endpoint(profile: GatewayProfile) -> str:
    """Full URL the TUI posts a prompt to for this profile."""
    base = profile.base_url.rstrip("/")
    if profile.mode == "helios":
        return f"{base}/v1/ai/complete"
    return f"{base}/chat/completions"


def request_headers(profile: GatewayProfile) -> dict[str, str]:
    """Auth + content headers for this profile."""
    headers = {"Content-Type": "application/json"}
    if profile.mode == "helios":
        key = profile.resolve_api_key()
        if key:
            headers["X-Helios-API-Key"] = key
        headers.update(profile.headers)
    else:
        headers.update(profile.auth_headers())
    return headers
