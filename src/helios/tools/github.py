"""
GitHub tools — the flagship integration.

Real REST executors (httpx). The token comes from HELIOS_GITHUB_TOKEN and is
never logged; the client is injectable for tests. Risk is contextual:
`github.read_file` is LOW while `github.merge_pr` into a protected branch
is CRITICAL and lands in the approval queue.
"""

from __future__ import annotations

import base64
from typing import Callable

import httpx

from helios.broker.manifest import ToolManifest
from helios.config import settings


_client_factory: Callable[[], httpx.Client] | None = None


def set_client_factory(factory: Callable[[], httpx.Client] | None) -> None:
    """Test hook: inject a stub httpx client."""
    global _client_factory
    _client_factory = factory


def _client() -> httpx.Client:
    if _client_factory is not None:
        return _client_factory()
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "helios-control-plane"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return httpx.Client(base_url=settings.github_api_base, headers=headers, timeout=20.0)


def _get(path: str, params: dict | None = None) -> dict | list:
    with _client() as client:
        response = client.get(path, params=params)
        response.raise_for_status()
        return response.json()


def _send(method: str, path: str, payload: dict) -> dict:
    with _client() as client:
        response = client.request(method, path, json=payload)
        response.raise_for_status()
        return response.json() if response.content else {}


# --- executors --------------------------------------------------------------


def _get_repo(args: dict, context) -> dict:
    data = _get(f"/repos/{args['repo']}")
    return {
        "repo": data.get("full_name"),
        "description": data.get("description"),
        "default_branch": data.get("default_branch"),
        "open_issues": data.get("open_issues_count"),
        "language": data.get("language"),
        "private": data.get("private"),
    }


def _read_file(args: dict, context) -> dict:
    params = {"ref": args["ref"]} if args.get("ref") else None
    data = _get(f"/repos/{args['repo']}/contents/{args['path']}", params)
    content = ""
    if isinstance(data, dict) and data.get("encoding") == "base64":
        content = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
    return {"repo": args["repo"], "path": args["path"],
            "content": content[: settings.tool_output_max_bytes],
            "sha": data.get("sha") if isinstance(data, dict) else None}


def _list_prs(args: dict, context) -> dict:
    data = _get(f"/repos/{args['repo']}/pulls",
                {"state": args.get("state") or "open", "per_page": 20})
    return {"repo": args["repo"], "pulls": [
        {"number": p.get("number"), "title": p.get("title"),
         "head": (p.get("head") or {}).get("ref"),
         "base": (p.get("base") or {}).get("ref"),
         "state": p.get("state"), "draft": p.get("draft")}
        for p in (data if isinstance(data, list) else [])
    ]}


def _create_branch(args: dict, context) -> dict:
    repo = args["repo"]
    source = args.get("from_ref")
    if not source:
        source = _get(f"/repos/{repo}").get("default_branch", "main")
    ref = _get(f"/repos/{repo}/git/ref/heads/{source}")
    sha = (ref.get("object") or {}).get("sha")
    created = _send("POST", f"/repos/{repo}/git/refs",
                    {"ref": f"refs/heads/{args['branch']}", "sha": sha})
    return {"repo": repo, "branch": args["branch"], "from": source,
            "ref": created.get("ref"), "sha": sha}


def _create_pr(args: dict, context) -> dict:
    data = _send("POST", f"/repos/{args['repo']}/pulls", {
        "title": args["title"],
        "head": args["head"],
        "base": args["base"],
        "body": args.get("body", ""),
    })
    return {"repo": args["repo"], "number": data.get("number"),
            "url": data.get("html_url"), "head": args["head"], "base": args["base"]}


def _merge_pr(args: dict, context) -> dict:
    data = _send("PUT", f"/repos/{args['repo']}/pulls/{int(args['number'])}/merge",
                 {"merge_method": args.get("method") or "merge"})
    return {"repo": args["repo"], "number": int(args["number"]),
            "merged": bool(data.get("merged")), "sha": data.get("sha"),
            "message": data.get("message")}


_OBJ = {"type": "object", "additionalProperties": False}
_REPO = {"repo": {"type": "string"}}


def install(registry) -> None:
    registry.register(ToolManifest(
        name="github.get_repo", description="Read repository metadata",
        capability="read", risk_class="low", scopes=["github.read"],
        input_schema={**_OBJ, "properties": {**_REPO}, "required": ["repo"]},
        resource_fields={"repo": "github.repo"},
        network=["api.github.com"], idempotent=True,
    ), _get_repo)

    registry.register(ToolManifest(
        name="github.read_file", description="Read a file from a repository",
        capability="read", risk_class="low", scopes=["github.read"],
        input_schema={**_OBJ, "properties": {
            **_REPO, "path": {"type": "string"}, "ref": {"type": "string"}},
            "required": ["repo", "path"]},
        resource_fields={"repo": "github.repo", "ref": "github.branch"},
        network=["api.github.com"], idempotent=True,
    ), _read_file)

    registry.register(ToolManifest(
        name="github.list_prs", description="List pull requests",
        capability="read", risk_class="low", scopes=["github.read"],
        input_schema={**_OBJ, "properties": {
            **_REPO, "state": {"type": "string", "enum": ["open", "closed", "all"]}},
            "required": ["repo"]},
        resource_fields={"repo": "github.repo"},
        network=["api.github.com"], idempotent=True,
    ), _list_prs)

    registry.register(ToolManifest(
        name="github.create_branch", description="Create a branch",
        capability="write", risk_class="low", scopes=["github.write"],
        input_schema={**_OBJ, "properties": {
            **_REPO, "branch": {"type": "string"}, "from_ref": {"type": "string"}},
            "required": ["repo", "branch"]},
        resource_fields={"repo": "github.repo", "branch": "github.branch"},
        network=["api.github.com"],
    ), _create_branch)

    registry.register(ToolManifest(
        name="github.create_pr", description="Open a pull request",
        capability="write", risk_class="low", scopes=["github.create_pr"],
        input_schema={**_OBJ, "properties": {
            **_REPO, "title": {"type": "string"}, "head": {"type": "string"},
            "base": {"type": "string"}, "body": {"type": "string"}},
            "required": ["repo", "title", "head", "base"]},
        # Note: opening a PR *against* main does not modify main — only the
        # merge tool maps its base branch into the protected-branch signal.
        resource_fields={"repo": "github.repo"},
        network=["api.github.com"], args_editable=True,
    ), _create_pr)

    registry.register(ToolManifest(
        name="github.merge_pr", description="Merge a pull request into its base branch",
        capability="write", risk_class="low", scopes=["github.merge"],
        input_schema={**_OBJ, "properties": {
            **_REPO, "number": {"type": "integer"},
            "base": {"type": "string"},
            "method": {"type": "string", "enum": ["merge", "squash", "rebase"]}},
            "required": ["repo", "number", "base"]},
        resource_fields={"repo": "github.repo", "base": "github.base"},
        network=["api.github.com"],
    ), _merge_pr)
