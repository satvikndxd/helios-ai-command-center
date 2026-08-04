import argparse
import secrets

from helios.db import SessionLocal, engine
from helios.models import ApiKey, Application, Base, Tenant, hash_api_key


def get_or_create_tenant(db, name: str) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.name == name).first()

    if tenant:
        return tenant

    tenant = Tenant(name=name)
    db.add(tenant)
    db.flush()

    return tenant


def get_or_create_application(db, tenant: Tenant, name: str) -> Application:
    application = (
        db.query(Application)
        .filter(
            Application.tenant_id == tenant.id,
            Application.name == name,
        )
        .first()
    )

    if application:
        return application

    application = Application(
        tenant_id=tenant.id,
        name=name,
    )
    db.add(application)
    db.flush()

    return application


def create_api_key(tenant_name: str, application_name: str) -> None:
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    try:
        tenant = get_or_create_tenant(db, tenant_name)
        application = get_or_create_application(db, tenant, application_name)

        raw_key = secrets.token_urlsafe(32)

        api_key = ApiKey(
            key_hash=hash_api_key(raw_key),
            tenant_id=tenant.id,
            application_id=application.id,
            name=f"{tenant_name}-{application_name}",
            active=True,
        )

        db.add(api_key)
        db.commit()

        print("Created API key:")
        print(raw_key)
        print()
        print("Example request:")
        print(
            f'curl -X POST http://localhost:8000/v1/ai/complete '
            f'-H "X-Helios-API-Key: {raw_key}" '
            f'-H "Content-Type: application/json" '
            f'-d \'{{"input": "Hello Helios"}}\''
        )

    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="helios")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_key_parser = subparsers.add_parser(
        "create-api-key",
        help="Create a tenant/application API key",
    )
    create_key_parser.add_argument("--tenant", required=True)
    create_key_parser.add_argument("--app", required=True)

    args = parser.parse_args()

    if args.command == "create-api-key":
        create_api_key(args.tenant, args.app)


if __name__ == "__main__":
    main()
