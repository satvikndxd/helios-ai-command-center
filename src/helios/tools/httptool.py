"""
Governed HTTP tool — GET only, domain-allowlisted (reuses the existing
web-access policy allowlist), output treated as untrusted external content
(the broker quarantines injection and scrubs secrets on the way back).
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from helios.broker.manifest import ToolManifest
from helios.config import settings
from helios.web.adapters.http_reader import html_to_text
from helios.web.policy import WebAccessPolicy


def _resource(args: dict) -> dict:
    host = (urlparse(str(args.get("url", ""))).hostname or "").lower()
    return {"http.domain": host}


def _get(args: dict, context) -> dict:
    url = str(args["url"])
    policy = WebAccessPolicy()
    if not policy.domain_allowed(url):
        raise PermissionError(f"domain not on the HTTP allowlist: {url}")
    with httpx.Client(timeout=20.0, follow_redirects=True,
                      headers={"User-Agent": "helios-control-plane"}) as client:
        response = client.get(url)
    text = response.text or ""
    if "html" in (response.headers.get("content-type") or ""):
        text, _title = html_to_text(text)
    return {"url": url, "status_code": response.status_code,
            "content": text[: settings.tool_output_max_bytes]}


def install(registry) -> None:
    registry.register(
        ToolManifest(
            name="http.get",
            description="Fetch a URL from an allowlisted domain (read-only)",
            capability="network",
            risk_class="low",
            scopes=["network.request"],
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
                "additionalProperties": False,
            },
            network=["allowlisted domains"],
            idempotent=True,
        ),
        _get,
        _resource,
    )
