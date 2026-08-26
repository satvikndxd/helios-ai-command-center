"""
Self-evolution routes: analyze -> review -> approve/apply -> rollback.

The agent proposes its own improvements from production evidence; humans
hold the gate.  Nothing self-approves.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.evolution import analyze, apply_proposal, evolution_state, rollback_proposal
from helios.models import ApiKey, EvolutionProposal
from helios.security import get_api_key

router = APIRouter(tags=["evolution"])


class DecideIn(BaseModel):
    decided_by: str = "operator"


def _serialize(p: EvolutionProposal) -> dict:
    return {
        "id": p.id,
        "kind": p.kind,
        "title": p.title,
        "change": p.change,
        "evidence": p.evidence,
        "validation": p.validation,
        "status": p.status,
        "version": p.version,
        "decided_by": p.decided_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _get(db: Session, api_key: ApiKey, proposal_id: str) -> EvolutionProposal:
    proposal = (
        db.query(EvolutionProposal)
        .filter(
            EvolutionProposal.id == proposal_id,
            EvolutionProposal.tenant_id == api_key.tenant_id,
        )
        .first()
    )
    if not proposal:
        raise HTTPException(status_code=404, detail="proposal not found")
    return proposal


@router.post("/v1/evolution/analyze")
async def run_analysis(
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """Mine recent traces -> cluster failures -> typed, evidenced proposals."""
    created = analyze(db, api_key.tenant_id)
    return {"created": [_serialize(p) for p in created]}


@router.get("/v1/evolution/proposals")
async def list_proposals(
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    proposals = (
        db.query(EvolutionProposal)
        .filter(EvolutionProposal.tenant_id == api_key.tenant_id)
        .order_by(EvolutionProposal.created_at.desc())
        .all()
    )
    return {"proposals": [_serialize(p) for p in proposals]}


@router.post("/v1/evolution/proposals/{proposal_id}/approve")
async def approve(
    proposal_id: str,
    payload: DecideIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    """HUMAN gate: approve and apply (versioned, rollback-able)."""
    proposal = _get(db, api_key, proposal_id)
    try:
        proposal = apply_proposal(db, proposal, payload.decided_by)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize(proposal)


@router.post("/v1/evolution/proposals/{proposal_id}/reject")
async def reject(
    proposal_id: str,
    payload: DecideIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    proposal = _get(db, api_key, proposal_id)
    if proposal.status != "proposed":
        raise HTTPException(status_code=409, detail=f"proposal is {proposal.status}")
    proposal.status = "rejected"
    proposal.decided_by = payload.decided_by
    db.commit()
    return _serialize(proposal)


@router.post("/v1/evolution/proposals/{proposal_id}/rollback")
async def rollback(
    proposal_id: str,
    payload: DecideIn,
    api_key: ApiKey = Depends(get_api_key),
    db: Session = Depends(get_db),
):
    proposal = _get(db, api_key, proposal_id)
    try:
        proposal = rollback_proposal(db, proposal, payload.decided_by)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _serialize(proposal)


@router.get("/v1/evolution/state")
async def get_state(api_key: ApiKey = Depends(get_api_key)):
    """The live evolution overrides currently applied for this tenant."""
    return {"state": evolution_state(api_key.tenant_id)}
