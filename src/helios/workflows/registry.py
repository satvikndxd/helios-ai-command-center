"""
Workspace registry — assembles domain packs into one catalog.

Adding a fourth domain = adding one pack module here (or registering at
runtime).  The engine and governance core never change per domain.
Pack actions register into the EXISTING typed-action registry so approvals,
payload binding, idempotency, and audit are shared, never duplicated.
"""

from __future__ import annotations

import hashlib

from helios.web.actions import register_action
from helios.workflows.types import WorkspacePack

_PACKS: dict[str, WorkspacePack] = {}


def _register_pack_actions(pack: WorkspacePack) -> None:
    for spec in pack.config.actions:
        def _executor(args: dict, _name=spec.name) -> dict:
            return {
                "prepared": _name,
                "workspace": args.get("workspace_id"),
                "execution_id": args.get("execution_id"),
                "summary_sha256": hashlib.sha256(
                    str(args.get("summary", "")).encode()
                ).hexdigest(),
            }

        register_action(
            spec.name,
            risk=spec.risk,
            description=spec.description,
            executor=_executor,
        )


def register_pack(pack: WorkspacePack) -> None:
    _PACKS[pack.config.id] = pack
    _register_pack_actions(pack)


def _load_builtin_packs() -> None:
    if _PACKS:
        return
    from helios.workflows.packs.engineering import PACK as engineering_pack
    from helios.workflows.packs.finance import PACK as finance_pack
    from helios.workflows.packs.software import PACK as software_pack

    for pack in (engineering_pack, software_pack, finance_pack):
        register_pack(pack)


def all_packs() -> dict[str, WorkspacePack]:
    _load_builtin_packs()
    return dict(_PACKS)


def get_pack(workspace_id: str) -> WorkspacePack:
    _load_builtin_packs()
    if workspace_id not in _PACKS:
        known = ", ".join(sorted(_PACKS))
        raise KeyError(f"Unknown workspace '{workspace_id}'. Known: {known}")
    return _PACKS[workspace_id]
