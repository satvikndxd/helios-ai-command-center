"""
P0 tool ecosystem: filesystem, safe shell, git, GitHub, HTTP, MCP.

Every module exposes `install(registry)`. Executors are only ever called by
the ToolBroker after permissions, risk, and policy have all said yes.
"""

from __future__ import annotations


def install_all(registry) -> None:
    from helios.tools import filesystem, github, gittool, httptool, mcptool, shell

    for module in (filesystem, shell, gittool, github, httptool, mcptool):
        module.install(registry)
