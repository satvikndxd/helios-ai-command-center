"""
Lightweight additive migrations.

The repo's documented stance (see `db.init_db`) is `create_all` for fresh
installs — no Alembic. V1.5 keeps that stance but must not break EXISTING
databases when it adds nullable columns to existing tables. This module runs
idempotent `ALTER TABLE ... ADD COLUMN` statements (supported identically by
SQLite and Postgres for nullable columns without defaults) after `create_all`.

Every entry is additive and default-safe: old rows keep working, old code
paths never see a NOT NULL surprise. Run manually with:

    PYTHONPATH=src python -m helios.migrate
"""

from __future__ import annotations

from sqlalchemy import inspect, text

# table -> [(column, DDL type)] — keep in sync with models.py additions.
COLUMN_ADDITIONS: dict[str, list[tuple[str, str]]] = {
    # Phase 2 (IDENTITY): bind sessions + evidence to registered AI systems.
    "agent_sessions": [("system_id", "VARCHAR(100)")],
    "trace_events": [("system_id", "VARCHAR(100)")],
    # Phase 7 (human oversight expansion) — nullable, default-safe.
    "approval_requests": [
        ("expires_at", "TIMESTAMP"),
        ("scope", "JSON"),
        ("comments", "JSON"),
        ("delegated_to", "VARCHAR(255)"),
        ("decision_kind", "VARCHAR(30)"),
        ("system_id", "VARCHAR(100)"),
    ],
}


def ensure_columns(verbose: bool = False) -> list[str]:
    """Add any missing nullable columns. Returns the list of changes applied."""
    from helios.db import engine

    applied: list[str] = []
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in COLUMN_ADDITIONS.items():
            if not inspector.has_table(table):
                continue  # create_all has not run for this table yet
            existing = {col["name"] for col in inspector.get_columns(table)}
            for name, ddl in columns:
                if name in existing:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                applied.append(f"{table}.{name}")
                if verbose:
                    print(f"[helios.migrate] ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    return applied


if __name__ == "__main__":
    from helios.db import init_db

    init_db()
    changes = ensure_columns(verbose=True)
    print(f"[helios.migrate] done — {len(changes)} column(s) added: {changes or 'none'}")
