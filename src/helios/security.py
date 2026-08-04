from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from helios.db import get_db
from helios.models import ApiKey, hash_api_key


async def get_api_key(
    x_helios_api_key: str | None = Header(default=None, alias="X-Helios-API-Key"),
    db: Session = Depends(get_db),
) -> ApiKey:
    """
    Authenticate requests using a SHA-256 hashed API key.

    Phase 1 uses simple API keys tied to tenant + application.
    Later phases can add OAuth2/OIDC, service accounts, and RBAC extensions.
    """

    if not x_helios_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Helios-API-Key header",
        )

    key_hash = hash_api_key(x_helios_api_key)

    api_key = (
        db.query(ApiKey)
        .filter(
            ApiKey.key_hash == key_hash,
            ApiKey.active.is_(True),
        )
        .first()
    )

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or inactive API key",
        )

    return api_key
