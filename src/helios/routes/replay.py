"""
Replay API (ASSURANCE plane).

    POST /v1/replay/systems/{system_id}   replay the system's recorded runs
                                          against candidate state

Complements the V1 single-run replay (POST /v1/agent/runs/{id}/replay,
unchanged): this endpoint answers the change-management question — "what
would THIS candidate policy/model/posture have done across the system's
recent history?" Nothing executes; every diff cites recorded evidence.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.governance.replay import replay_system
from helios.governance.systems import get_system
from helios.models import ApiKey, GovernancePolicySet as PolicySetRow
from helios.security import get_api_key

router = APIRouter(prefix="/v1/replay", tags=["replay"])


class SystemReplayIn(BaseModel):
    candidate_tool_policy: dict | None = None
    candidate_governance: dict | None = None       # inline policy-set document
    candidate_governance_set_id: str | None = None  # or a stored (candidate) set
    candidate_system: dict | None = None           # posture overrides
    candidate_model: dict | None = None            # registry-state overrides
    run_limit: int = Field(default=25, ge=1, le=200)


@router.post("/systems/{system_id}")
def replay_system_route(
    system_id: str,
    payload: SystemReplayIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    if get_system(db, api_key.tenant_id, system_id) is None:
        raise HTTPException(status_code=404,
                            detail=f"ai system '{system_id}' not found")

    governance_doc = payload.candidate_governance
    if payload.candidate_governance_set_id:
        row = (
            db.query(PolicySetRow)
            .filter(PolicySetRow.id == payload.candidate_governance_set_id,
                    PolicySetRow.tenant_id == api_key.tenant_id)
            .first()
        )
        if row is None:
            raise HTTPException(status_code=404, detail="candidate policy set not found")
        governance_doc = {
            "name": row.name, "version": row.version, "rules": row.rules,
            "description": row.description, "default_effect": row.default_effect,
            "scope": row.scope or {},
        }

    return replay_system(
        db, api_key.tenant_id, system_id,
        candidate_tool_policy=payload.candidate_tool_policy,
        candidate_governance_set=governance_doc,
        candidate_system=payload.candidate_system,
        candidate_model=payload.candidate_model,
        run_limit=payload.run_limit,
    )
