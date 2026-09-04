"""
Tool registry — manifested tools only.

A tool is (manifest, executor, optional resource resolver). Registration is
additive with no silent overrides, mirroring the proven action-registry
semantics. `default_registry()` assembles the P0 toolset: filesystem, safe
shell, git, GitHub, HTTP, MCP.
"""

from __future__ import annotations

from typing import Callable

from helios.broker.manifest import Tool, ToolManifest


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(
        self,
        manifest: ToolManifest,
        executor: Callable[..., dict],
        resource_fn: Callable[[dict], dict] | None = None,
    ) -> None:
        existing = self._tools.get(manifest.name)
        if existing is not None:
            if existing.manifest.model_dump() == manifest.model_dump():
                return  # identical re-registration is a no-op
            raise ValueError(
                f"tool '{manifest.name}' already registered with a different manifest"
            )
        self._tools[manifest.name] = Tool(manifest, executor, resource_fn)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[ToolManifest]:
        return [t.manifest for _, t in sorted(self._tools.items())]


_default: ToolRegistry | None = None


def default_registry() -> ToolRegistry:
    """The process-wide registry with the P0 toolset installed."""
    global _default
    if _default is None:
        registry = ToolRegistry()
        from helios.tools import install_all

        install_all(registry)
        _default = registry
    return _default


def reset_default_registry() -> None:
    """Test hook."""
    global _default
    _default = None
