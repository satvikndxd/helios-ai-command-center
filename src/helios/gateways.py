"""
Gateway catalog: data-driven profiles for every OpenAI-compatible endpoint
Helios can talk to.

Two kinds of profiles:

* **Built-in** — a seed catalog of hosted providers, aggregators, enterprise
  gateways, and local runtimes.  These are plain data, not code: when a
  gateway exposes ``GET /models`` we discover its current models at runtime
  instead of hard-coding a model list that would rot.
* **Custom** — user-defined profiles persisted as JSON (default
  ``~/.helios/gateways.json``, override with ``HELIOS_GATEWAYS_PATH``).

Credentials are **never** stored in a profile.  Profiles carry only the name
of an environment variable (``api_key_env``); the raw key is resolved from
the process environment at call time.  This makes profiles safe to commit.

Modes:

* ``helios``  — governed path through ``POST /v1/ai/complete`` (traces,
  policy, routing, evaluation are preserved).
* ``direct``  — the standard OpenAI Chat Completions contract against
  ``{base_url}/chat/completions`` (for local development and model
  exploration; Helios governance is bypassed and the TUI labels it DIRECT).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_GATEWAYS_PATH = "~/.helios/gateways.json"

# Values that look like raw secrets rather than environment-variable names.
_SECRET_LIKE = re.compile(r"^(sk-|gsk_|pk-|xai-|hf_|r8_|pplx-|nvapi-|Bearer )")


@dataclass
class GatewayProfile:
    """A connection profile for one gateway. Contains no secrets."""

    name: str
    base_url: str
    provider: str = "custom"
    mode: str = "direct"  # "direct" (OpenAI-compatible) or "helios" (governed)
    api_key_env: str | None = None
    default_model: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 120.0
    source: str = "custom"  # "builtin" or "custom"

    def resolve_api_key(self) -> str | None:
        """Resolve the credential from the environment at call time."""
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env) or None

    def auth_headers(self) -> dict[str, str]:
        headers = dict(self.headers)
        key = self.resolve_api_key()
        if key:
            headers.setdefault("Authorization", f"Bearer {key}")
        return headers

    def to_public_dict(self) -> dict[str, Any]:
        """Serializable form. By construction it contains no secret material."""
        data = asdict(self)
        return data


def _b(
    name: str,
    base_url: str,
    provider: str,
    api_key_env: str | None = None,
    default_model: str | None = None,
    mode: str = "direct",
) -> GatewayProfile:
    return GatewayProfile(
        name=name,
        base_url=base_url,
        provider=provider,
        mode=mode,
        api_key_env=api_key_env,
        default_model=default_model,
        source="builtin",
    )


def builtin_gateways() -> dict[str, GatewayProfile]:
    """
    The seed catalog.  Deliberately data-driven and extensible: model lists
    are discovered via ``GET /models`` (see `discover_models`), not pinned.
    """
    profiles = [
        # The governed Helios path — the default.
        GatewayProfile(
            name="helios",
            base_url=os.environ.get("HELIOS_BASE_URL", "http://localhost:8000"),
            provider="helios",
            mode="helios",
            api_key_env="HELIOS_API_KEY",
            default_model=None,  # the Helios router chooses
            source="builtin",
        ),
        # Hosted providers (OpenAI-compatible chat completions).
        _b("openai", "https://api.openai.com/v1", "openai", "OPENAI_API_KEY", "gpt-4o-mini"),
        _b("openrouter", "https://openrouter.ai/api/v1", "openrouter", "OPENROUTER_API_KEY", "openrouter/auto"),
        _b("groq", "https://api.groq.com/openai/v1", "groq", "GROQ_API_KEY", "llama-3.3-70b-versatile"),
        _b("together", "https://api.together.xyz/v1", "together", "TOGETHER_API_KEY", "meta-llama/Llama-3.3-70B-Instruct-Turbo"),
        _b("fireworks", "https://api.fireworks.ai/inference/v1", "fireworks", "FIREWORKS_API_KEY", "accounts/fireworks/models/llama-v3p3-70b-instruct"),
        _b("deepinfra", "https://api.deepinfra.com/v1/openai", "deepinfra", "DEEPINFRA_API_KEY", "meta-llama/Meta-Llama-3.1-70B-Instruct"),
        _b("hyperbolic", "https://api.hyperbolic.xyz/v1", "hyperbolic", "HYPERBOLIC_API_KEY", "meta-llama/Meta-Llama-3.1-70B-Instruct"),
        _b("nvidia", "https://integrate.api.nvidia.com/v1", "nvidia", "NVIDIA_API_KEY", "meta/llama-3.1-70b-instruct"),
        _b("cerebras", "https://api.cerebras.ai/v1", "cerebras", "CEREBRAS_API_KEY", "llama-3.3-70b"),
        _b("sambanova", "https://api.sambanova.ai/v1", "sambanova", "SAMBANOVA_API_KEY", "Meta-Llama-3.3-70B-Instruct"),
        _b("deepseek", "https://api.deepseek.com/v1", "deepseek", "DEEPSEEK_API_KEY", "deepseek-chat"),
        _b("mistral", "https://api.mistral.ai/v1", "mistral", "MISTRAL_API_KEY", "mistral-small-latest"),
        _b("xai", "https://api.x.ai/v1", "xai", "XAI_API_KEY", "grok-2-latest"),
        _b("cohere", "https://api.cohere.ai/compatibility/v1", "cohere", "COHERE_API_KEY", "command-r-plus"),
        _b("perplexity", "https://api.perplexity.ai", "perplexity", "PERPLEXITY_API_KEY", "sonar"),
        _b("huggingface", "https://router.huggingface.co/v1", "huggingface", "HF_TOKEN", None),
        _b("cloudflare", "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1", "cloudflare", "CLOUDFLARE_API_TOKEN", "@cf/meta/llama-3.1-8b-instruct"),
        # Aggregators / enterprise gateways.
        _b("litellm", "http://localhost:4000/v1", "litellm", "LITELLM_API_KEY", None),
        _b("portkey", "https://api.portkey.ai/v1", "portkey", "PORTKEY_API_KEY", None),
        # Local OpenAI-compatible runtimes — no API key required.
        _b("ollama", "http://localhost:11434/v1", "ollama", None, "llama3.2"),
        _b("lmstudio", "http://localhost:1234/v1", "lmstudio", None, None),
        _b("vllm", "http://localhost:8001/v1", "vllm", None, None),
        _b("llamacpp", "http://localhost:8080/v1", "llamacpp", None, None),
        _b("sglang", "http://localhost:30000/v1", "sglang", None, None),
        _b("localai", "http://localhost:8081/v1", "localai", None, None),
    ]
    return {p.name: p for p in profiles}


def gateways_path(path: str | os.PathLike[str] | None = None) -> Path:
    raw = path or os.environ.get("HELIOS_GATEWAYS_PATH", DEFAULT_GATEWAYS_PATH)
    return Path(raw).expanduser()


def load_custom_gateways(
    path: str | os.PathLike[str] | None = None,
) -> dict[str, GatewayProfile]:
    file = gateways_path(path)
    if not file.exists():
        return {}
    data = json.loads(file.read_text())
    profiles: dict[str, GatewayProfile] = {}
    for entry in data.get("gateways", []):
        entry = {k: v for k, v in entry.items() if k in GatewayProfile.__dataclass_fields__}
        entry["source"] = "custom"
        profile = GatewayProfile(**entry)
        profiles[profile.name] = profile
    return profiles


def all_gateways(
    path: str | os.PathLike[str] | None = None,
) -> dict[str, GatewayProfile]:
    """Built-in catalog with custom profiles layered on top (custom wins)."""
    profiles = builtin_gateways()
    profiles.update(load_custom_gateways(path))
    return profiles


def get_gateway(
    name: str,
    path: str | os.PathLike[str] | None = None,
) -> GatewayProfile:
    profiles = all_gateways(path)
    if name not in profiles:
        known = ", ".join(sorted(profiles))
        raise KeyError(f"Unknown gateway '{name}'. Known gateways: {known}")
    return profiles[name]


def add_custom_gateway(
    profile: GatewayProfile,
    path: str | os.PathLike[str] | None = None,
) -> Path:
    """
    Persist a custom profile.  Refuses anything that looks like a raw secret
    in ``api_key_env`` — profiles store environment-variable *names* only.
    """
    if profile.api_key_env and (
        _SECRET_LIKE.match(profile.api_key_env) or len(profile.api_key_env) > 128
    ):
        raise ValueError(
            "api_key_env must be the NAME of an environment variable, "
            "never the credential itself."
        )
    for value in profile.headers.values():
        if _SECRET_LIKE.match(value):
            raise ValueError("headers must not contain raw credentials.")

    file = gateways_path(path)
    file.parent.mkdir(parents=True, exist_ok=True)

    existing = load_custom_gateways(path)
    profile.source = "custom"
    existing[profile.name] = profile

    payload = {"gateways": [p.to_public_dict() for p in existing.values()]}
    file.write_text(json.dumps(payload, indent=2) + "\n")
    return file


def normalize_models_payload(payload: Any) -> list[str]:
    """
    Normalize a ``GET /models`` response into a sorted list of model IDs.

    Handles the OpenAI shape ``{"data": [{"id": ...}]}``, the bare-list
    shape ``[{"id": ...}]``, and ``{"models": [{"name"|"id"|"model": ...}]}``
    (Ollama native / Gemini-style listings).
    """
    items: list[Any]
    if isinstance(payload, dict):
        items = payload.get("data") or payload.get("models") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    ids: list[str] = []
    for item in items:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("name") or item.get("model")
            if model_id:
                ids.append(str(model_id))
    return sorted(set(ids))


def discover_models(profile: GatewayProfile, client: Any = None) -> list[str]:
    """
    Discover the gateway's current models via ``GET {base_url}/models``.

    ``client`` is any object with a ``get(url, headers=..., timeout=...)``
    method returning an object with ``.json()`` (httpx.Client in production,
    a stub in tests).
    """
    if client is None:
        import httpx

        client = httpx

    url = profile.base_url.rstrip("/") + "/models"
    response = client.get(url, headers=profile.auth_headers(), timeout=profile.timeout_s)
    if hasattr(response, "raise_for_status"):
        response.raise_for_status()
    return normalize_models_payload(response.json())
