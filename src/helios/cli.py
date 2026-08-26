import argparse
import secrets

from helios.db import SessionLocal, init_db
from helios.gateways import GatewayProfile, add_custom_gateway, all_gateways
from helios.models import ApiKey, Application, Tenant, hash_api_key


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
    init_db()

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


def gateway_add(args: argparse.Namespace) -> None:
    headers = {}
    for raw in args.header or []:
        if ":" not in raw:
            raise SystemExit(f"Invalid --header '{raw}', expected 'Name: value'")
        name, _, value = raw.partition(":")
        headers[name.strip()] = value.strip()

    profile = GatewayProfile(
        name=args.name,
        base_url=args.base_url,
        provider=args.provider,
        mode=args.mode,
        api_key_env=args.api_key_env,
        default_model=args.model,
        headers=headers,
        timeout_s=args.timeout,
    )
    path = add_custom_gateway(profile)

    print(f"Saved gateway '{profile.name}' to {path}")
    if profile.api_key_env:
        print(f"Credential is read from ${profile.api_key_env} at call time; "
              "nothing secret was written to disk.")


def gateway_list() -> None:
    profiles = all_gateways()
    width = max(len(name) for name in profiles) + 2
    print(f"{'NAME':<{width}}{'MODE':<9}{'SOURCE':<9}{'KEY ENV':<26}BASE URL")
    for name in sorted(profiles):
        p = profiles[name]
        print(
            f"{p.name:<{width}}{p.mode:<9}{p.source:<9}"
            f"{(p.api_key_env or '-'):<26}{p.base_url}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="helios")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_key_parser = subparsers.add_parser(
        "create-api-key",
        help="Create a tenant/application API key",
    )
    create_key_parser.add_argument("--tenant", required=True)
    create_key_parser.add_argument("--app", required=True)

    gateway_add_parser = subparsers.add_parser(
        "gateway-add",
        help="Save a custom gateway profile (no secrets are stored)",
    )
    gateway_add_parser.add_argument("name")
    gateway_add_parser.add_argument("--base-url", required=True)
    gateway_add_parser.add_argument("--provider", default="custom")
    gateway_add_parser.add_argument(
        "--api-key-env",
        default=None,
        help="NAME of the environment variable holding the API key",
    )
    gateway_add_parser.add_argument("--model", default=None, help="Default model id")
    gateway_add_parser.add_argument("--mode", choices=["direct", "helios"], default="direct")
    gateway_add_parser.add_argument(
        "--header",
        action="append",
        help="Extra header as 'Name: value' (repeatable, no credentials)",
    )
    gateway_add_parser.add_argument("--timeout", type=float, default=120.0)

    subparsers.add_parser(
        "gateway-list",
        help="List built-in and custom gateway profiles",
    )

    args = parser.parse_args()

    if args.command == "create-api-key":
        create_api_key(args.tenant, args.app)
    elif args.command == "gateway-add":
        gateway_add(args)
    elif args.command == "gateway-list":
        gateway_list()


if __name__ == "__main__":
    main()
