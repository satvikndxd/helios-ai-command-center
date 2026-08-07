import os
import tempfile

# Point Helios at a throwaway SQLite database BEFORE any helios module imports,
# so the whole test suite runs with zero external dependencies (no Postgres).
_tmp_db = os.path.join(tempfile.mkdtemp(prefix="helios-test-"), "helios.sqlite3")
os.environ.setdefault("HELIOS_DATABASE_URL", f"sqlite:///{_tmp_db}")
os.environ.setdefault("HELIOS_DEFAULT_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from helios.db import SessionLocal, engine  # noqa: E402
from helios.main import app  # noqa: E402
from helios.models import Base  # noqa: E402
from helios.cli import get_or_create_application, get_or_create_tenant  # noqa: E402
from helios.models import ApiKey, hash_api_key  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _create_schema():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client():
    return TestClient(app)


def _mint_key(tenant_name: str, app_name: str, raw_key: str) -> str:
    db = SessionLocal()
    try:
        tenant = get_or_create_tenant(db, tenant_name)
        app_row = get_or_create_application(db, tenant, app_name)
        existing = (
            db.query(ApiKey).filter(ApiKey.key_hash == hash_api_key(raw_key)).first()
        )
        if not existing:
            db.add(
                ApiKey(
                    key_hash=hash_api_key(raw_key),
                    tenant_id=tenant.id,
                    application_id=app_row.id,
                    name="test",
                    active=True,
                )
            )
        db.commit()
    finally:
        db.close()
    return raw_key


@pytest.fixture()
def api_key() -> str:
    """Tenant 'acme': raw API key usable in requests."""
    return _mint_key("acme", "support", "test-key-abc123")


@pytest.fixture()
def other_tenant_api_key() -> str:
    """A DIFFERENT tenant ('globex') for isolation tests."""
    return _mint_key("globex", "support", "test-key-globex-999")
