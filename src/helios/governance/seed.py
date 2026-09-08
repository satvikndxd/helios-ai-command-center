"""
Reproducible governance demo seeding.

Registers an approved model and a `release-agent` AI system (L3, staging,
GitHub-bound) so a user can immediately explore the governance control
center: /systems, /governance, /decisions, /audit. No network required.
"""

from __future__ import annotations

from helios.db import SessionLocal
from helios.governance.model_registry import register_model
from helios.governance.systems import create_system


def seed_governance_demo(tenant_id: str) -> dict:
    db = SessionLocal()
    try:
        try:
            register_model(db, tenant_id, {
                "provider": "scripted", "model_id": "release-model", "version": "17",
                "status": "approved", "risk_class": "medium",
                "allowed_data_classes": ["public", "internal"],
                "allowed_environments": ["dev", "staging", "production"],
                "notes": "demo model",
            }, author="demo")
        except ValueError:
            pass  # already registered

        from helios.governance.systems import get_system
        if get_system(db, tenant_id, "release-agent") is None:
            create_system(db, tenant_id, {
                "system_id": "release-agent",
                "name": "Release Agent",
                "owner": "release-platform",
                "purpose": "ship approved code changes to the production branch",
                "environment": "staging",
                "autonomy_level": 3,
                "risk_class": "high",
                "models": [{"provider": "scripted", "model_id": "release-model"}],
                "tools": ["github.*", "fs.*", "shell.run", "git.*"],
                "data_classes": ["internal"],
                "oversight": {"production_merge": "human_required"},
            }, author="demo")
        return {"system": "release-agent", "model": "scripted/release-model"}
    finally:
        db.close()
