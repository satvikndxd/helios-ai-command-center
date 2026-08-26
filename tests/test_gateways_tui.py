"""Gateway catalog + TUI helper tests — offline, no network, no terminal."""

import json

import pytest

from helios.gateways import (
    GatewayProfile,
    add_custom_gateway,
    all_gateways,
    builtin_gateways,
    discover_models,
    get_gateway,
    load_custom_gateways,
    normalize_models_payload,
)
from helios.tui import (
    build_direct_payload,
    build_governed_payload,
    completion_endpoint,
    extract_direct_output,
    extract_governed_output,
    request_headers,
)


# -- catalog ---------------------------------------------------------------


def test_builtin_catalog_covers_hosted_and_local_gateways():
    catalog = builtin_gateways()

    for name in (
        "helios", "openai", "openrouter", "groq", "together", "fireworks",
        "deepinfra", "hyperbolic", "nvidia", "cerebras", "sambanova",
        "deepseek", "mistral", "xai", "cohere", "perplexity", "huggingface",
        "cloudflare", "litellm", "portkey", "ollama", "lmstudio", "vllm",
        "llamacpp", "sglang", "localai",
    ):
        assert name in catalog, f"missing builtin gateway: {name}"

    # The governed path is the only "helios"-mode builtin; local runtimes
    # need no API key.
    assert catalog["helios"].mode == "helios"
    assert catalog["ollama"].api_key_env is None
    assert all(p.source == "builtin" for p in catalog.values())


def test_custom_gateway_roundtrip_stores_no_secret(tmp_path):
    path = tmp_path / "gateways.json"
    profile = GatewayProfile(
        name="my-gateway",
        base_url="https://gateway.example.com/v1",
        provider="custom",
        api_key_env="MY_GATEWAY_API_KEY",
        default_model="my-model",
    )
    add_custom_gateway(profile, path)

    raw = path.read_text()
    assert "MY_GATEWAY_API_KEY" in raw          # the env-var NAME is stored
    assert "sk-" not in raw                     # no raw credential material

    loaded = load_custom_gateways(path)
    assert loaded["my-gateway"].base_url == "https://gateway.example.com/v1"
    assert loaded["my-gateway"].source == "custom"

    merged = all_gateways(path)
    assert "my-gateway" in merged and "ollama" in merged


def test_custom_gateway_rejects_raw_secrets(tmp_path):
    path = tmp_path / "gateways.json"
    with pytest.raises(ValueError):
        add_custom_gateway(
            GatewayProfile(name="bad", base_url="http://x", api_key_env="sk-abc123"),
            path,
        )
    with pytest.raises(ValueError):
        add_custom_gateway(
            GatewayProfile(
                name="bad2",
                base_url="http://x",
                headers={"Authorization": "Bearer sk-raw"},
            ),
            path,
        )
    assert not path.exists()


def test_custom_profile_overrides_builtin(tmp_path):
    path = tmp_path / "gateways.json"
    add_custom_gateway(
        GatewayProfile(name="ollama", base_url="http://box:11434/v1", provider="ollama"),
        path,
    )
    assert get_gateway("ollama", path).base_url == "http://box:11434/v1"


def test_get_gateway_unknown_name_lists_alternatives():
    with pytest.raises(KeyError) as exc:
        get_gateway("does-not-exist")
    assert "Known gateways" in exc.value.args[0]


# -- model discovery -------------------------------------------------------


def test_normalize_models_payload_shapes():
    openai_shape = {"data": [{"id": "b-model"}, {"id": "a-model"}]}
    ollama_shape = {"models": [{"name": "llama3.2"}, {"model": "qwen"}]}
    bare_list = ["z", {"id": "y"}]

    assert normalize_models_payload(openai_shape) == ["a-model", "b-model"]
    assert normalize_models_payload(ollama_shape) == ["llama3.2", "qwen"]
    assert normalize_models_payload(bare_list) == ["y", "z"]
    assert normalize_models_payload({"weird": True}) == []


def test_discover_models_hits_models_endpoint_with_auth(monkeypatch):
    monkeypatch.setenv("FAKE_KEY", "secret-token")
    profile = GatewayProfile(
        name="fake", base_url="https://fake.example/v1/", api_key_env="FAKE_KEY"
    )
    calls = {}

    class StubResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "m1"}, {"id": "m0"}]}

    class StubClient:
        def get(self, url, headers=None, timeout=None):
            calls["url"], calls["headers"] = url, headers
            return StubResponse()

    assert discover_models(profile, StubClient()) == ["m0", "m1"]
    assert calls["url"] == "https://fake.example/v1/models"
    assert calls["headers"]["Authorization"] == "Bearer secret-token"


# -- TUI helpers -----------------------------------------------------------


def test_governed_endpoint_payload_and_headers(monkeypatch):
    monkeypatch.setenv("HELIOS_API_KEY", "helios-key")
    profile = GatewayProfile(
        name="helios",
        base_url="http://localhost:8000",
        mode="helios",
        api_key_env="HELIOS_API_KEY",
    )

    assert completion_endpoint(profile) == "http://localhost:8000/v1/ai/complete"
    assert request_headers(profile)["X-Helios-API-Key"] == "helios-key"
    assert build_governed_payload("hi") == {"input": "hi"}
    assert build_governed_payload("hi", "m")["model"] == "m"


def test_direct_endpoint_payload_and_extraction(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gk")
    profile = get_gateway("groq")

    assert completion_endpoint(profile).endswith("/openai/v1/chat/completions")
    assert request_headers(profile)["Authorization"] == "Bearer gk"

    payload = build_direct_payload("hello", "llama-3.3-70b-versatile",
                                   [{"role": "user", "content": "earlier"}])
    assert payload["messages"][-1] == {"role": "user", "content": "hello"}
    assert len(payload["messages"]) == 2

    assert extract_direct_output(
        {"choices": [{"message": {"content": "hi there"}}]}
    ) == "hi there"
    assert extract_direct_output({}) == ""

    governed = extract_governed_output(
        {"output": "ok", "trace_id": "t1", "citations": [1]}
    )
    assert governed["output"] == "ok" and governed["trace_id"] == "t1"


def test_gateway_add_cli_persists_profile(tmp_path, monkeypatch, capsys):
    from helios import cli

    monkeypatch.setenv("HELIOS_GATEWAYS_PATH", str(tmp_path / "gw.json"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "helios", "gateway-add", "my-gateway",
            "--base-url", "https://gateway.example.com/v1",
            "--provider", "custom",
            "--api-key-env", "MY_GATEWAY_API_KEY",
            "--model", "my-model",
        ],
    )
    cli.main()
    out = capsys.readouterr().out
    assert "Saved gateway 'my-gateway'" in out

    saved = json.loads((tmp_path / "gw.json").read_text())
    assert saved["gateways"][0]["name"] == "my-gateway"
    assert saved["gateways"][0]["api_key_env"] == "MY_GATEWAY_API_KEY"

    monkeypatch.setattr("sys.argv", ["helios", "gateway-list"])
    cli.main()
    listing = capsys.readouterr().out
    assert "my-gateway" in listing and "ollama" in listing
