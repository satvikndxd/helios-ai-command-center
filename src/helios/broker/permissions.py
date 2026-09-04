"""
Permission scopes with resource constraints.

Not a boolean allow/deny matrix. A grant is:

    scope:        "github.write"          (or "github.*", "*")
    constraints:  [{"field": "github.repo",   "op": "eq", "value": "acme/api"},
                   {"field": "github.branch", "op": "ne", "value": "main"}]
    environments: ["dev", "staging"]      (empty = any)

Grants also carry organization/project/agent/user dimensions so the same
tenant can hand different agents different reach. Grants are plain JSON
dicts end-to-end: storable on a session, attachable to a trace, replayable.
"""

from __future__ import annotations

import fnmatch
from typing import Any, Iterable

from helios.broker.types import PermissionDecision


_OPS = ("eq", "ne", "prefix", "glob", "in", "not_in")


def _constraint_matches(constraint: dict, resource: dict) -> tuple[bool, str]:
    """Evaluate one resource constraint. Returns (ok, reason)."""
    field = constraint.get("field", "")
    op = constraint.get("op", "eq")
    expected = constraint.get("value")
    actual = resource.get(field)

    if actual is None:
        # The action does not target this resource dimension; constraint is
        # vacuously satisfied (e.g. branch constraint on a repo-level read).
        return True, f"{field} not targeted"

    actual_s = str(actual)
    if op == "eq":
        ok = actual_s == str(expected)
    elif op == "ne":
        ok = actual_s != str(expected)
    elif op == "prefix":
        ok = actual_s.startswith(str(expected))
    elif op == "glob":
        ok = fnmatch.fnmatch(actual_s, str(expected))
    elif op == "in":
        ok = actual_s in [str(v) for v in (expected or [])]
    elif op == "not_in":
        ok = actual_s not in [str(v) for v in (expected or [])]
    else:
        return False, f"unknown constraint op '{op}'"

    verdict = "satisfied" if ok else "violated"
    return ok, f"{field} {op} {expected!r} {verdict} (actual={actual_s!r})"


def scope_matches(granted: str, required: str) -> bool:
    """'github.write' matches grants 'github.write', 'github.*', '*'."""
    if granted == "*" or granted == required:
        return True
    if granted.endswith(".*"):
        return required.split(".", 1)[0] == granted[:-2]
    return False


class PermissionSet:
    """A set of grants evaluated deterministically against a required scope."""

    def __init__(self, grants: Iterable[dict]):
        self.grants = [dict(g) for g in grants]

    def to_list(self) -> list[dict]:
        return [dict(g) for g in self.grants]

    def check(
        self,
        scope: str,
        resource: dict,
        context,
    ) -> PermissionDecision:
        """
        Find a grant that (a) covers the scope, (b) applies to this context,
        and (c) whose every resource constraint is satisfied. Deny-by-default.
        """
        reasons: list[str] = []
        for grant in self.grants:
            granted_scope = grant.get("scope", "")
            if not scope_matches(granted_scope, scope):
                continue

            envs = grant.get("environments") or []
            if envs and context.environment not in envs:
                reasons.append(
                    f"grant '{granted_scope}' not valid in environment "
                    f"'{context.environment}' (allowed: {envs})"
                )
                continue

            mismatch = _context_mismatch(grant, context)
            if mismatch:
                reasons.append(f"grant '{granted_scope}' skipped: {mismatch}")
                continue

            constraint_reasons: list[str] = []
            ok = True
            for constraint in grant.get("constraints") or []:
                c_ok, c_reason = _constraint_matches(constraint, resource)
                constraint_reasons.append(c_reason)
                if not c_ok:
                    ok = False
            if ok:
                return PermissionDecision(
                    allowed=True,
                    scope=scope,
                    reasons=[f"granted by scope '{granted_scope}'"] + constraint_reasons,
                    matched_grant=dict(grant),
                )
            reasons.append(
                f"grant '{granted_scope}' matched scope but constraints failed: "
                + "; ".join(r for r in constraint_reasons if "violated" in r)
            )

        if not reasons:
            reasons.append(f"no grant covers scope '{scope}' (deny by default)")
        return PermissionDecision(allowed=False, scope=scope, reasons=reasons)


def _context_mismatch(grant: dict, context) -> str | None:
    """Grants may pin org/project/agent/user; a pinned value must match."""
    for dim, actual in (
        ("organization", context.organization),
        ("project", context.project),
        ("agent_id", context.agent_id),
        ("user_id", context.user_id),
    ):
        pinned = grant.get(dim)
        if pinned is not None and pinned != actual:
            return f"{dim} pinned to {pinned!r}, context has {actual!r}"
    return None


def developer_grants(
    *,
    workspace_root: str,
    github_repo: str | None = None,
    environments: list[str] | None = None,
) -> list[dict]:
    """
    The default grant profile for a local developer agent session:
    full read/write inside the workspace, shell, git, HTTP; GitHub scoped to
    one repository. Merge scope is granted but policy still gates it behind
    approval — grants say *may*, policy says *how*.
    """
    grants: list[dict] = [
        {"scope": "filesystem.read", "constraints": [
            {"field": "filesystem.path", "op": "prefix", "value": workspace_root}]},
        {"scope": "filesystem.write", "constraints": [
            {"field": "filesystem.path", "op": "prefix", "value": workspace_root}]},
        {"scope": "shell.execute", "constraints": [
            {"field": "shell.cwd", "op": "prefix", "value": workspace_root}]},
        {"scope": "git.read"},
        {"scope": "git.write", "constraints": [
            {"field": "git.branch", "op": "ne", "value": "main"}]},
        {"scope": "network.request"},
        {"scope": "mcp.call"},
    ]
    gh_constraints = (
        [{"field": "github.repo", "op": "eq", "value": github_repo}] if github_repo else []
    )
    for gh_scope in ("github.read", "github.write", "github.create_pr", "github.merge"):
        grants.append({"scope": gh_scope, "constraints": list(gh_constraints)})
    if environments:
        for g in grants:
            g["environments"] = list(environments)
    return grants
