"""Operator-only administrative commands; no command is exposed through HTTP."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import uuid
from datetime import UTC, datetime

from alos.audit import AuditEvent, SqlAuditRepository
from alos.authentication.repository import SqlAuthRepository
from alos.authentication.service import AuthService
from alos.config import Settings
from alos.persistence.database import Database
from alos.security.errors import PlatformError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alos-admin")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser(
        "bootstrap-identity",
        help="create the first Backend-owned identity administrator",
    )
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--display-name", required=True)
    bootstrap.add_argument("--tenant-id", required=True)
    bootstrap.add_argument("--organization-id", required=True)
    bootstrap.add_argument("--workspace-id", required=True)
    bootstrap.add_argument("--workspace-key", required=True)
    bootstrap.add_argument("--workspace-name", required=True)
    bootstrap.add_argument(
        "--workspace-type",
        choices=("EXECUTIVE", "BUSINESS", "IT_OPERATIONS", "GOVERNANCE", "SHARED"),
        default="IT_OPERATIONS",
    )
    bootstrap.add_argument("--organizational-unit-id")
    bootstrap.add_argument("--division-code")
    return parser


async def bootstrap_identity(args: argparse.Namespace, password: str) -> dict[str, object]:
    settings = Settings()
    database = Database(settings.DATABASE_URL)
    service = AuthService(SqlAuthRepository(database.session_factory))
    audit = SqlAuditRepository(database.session_factory)
    correlation_id = f"identity_bootstrap_{uuid.uuid4().hex}"
    try:
        result = await service.bootstrap_initial_admin(
            {
                "email": args.email,
                "password": password,
                "display_name": args.display_name,
                "tenant_id": args.tenant_id,
                "organization_id": args.organization_id,
                "workspace_id": args.workspace_id,
                "workspace_key": args.workspace_key,
                "workspace_name": args.workspace_name,
                "workspace_type": args.workspace_type,
                "organizational_unit_id": args.organizational_unit_id,
                "division_code": args.division_code,
            }
        )
        actor = result["actor"]
        assert isinstance(actor, dict)
        actor_id = str(actor["actor_id"])
        await audit.append(
            AuditEvent(
                event_type="identity.initial_authority.bootstrapped",
                entity_type="actor",
                entity_id=actor_id,
                tenant_id=args.tenant_id,
                organization_id=args.organization_id,
                workspace_id=args.workspace_id,
                actor_id=actor_id,
                actor_kind="SYSTEM",
                correlation_id=correlation_id,
                outcome="SUCCEEDED",
                occurred_at=datetime.now(UTC),
                reason="Initial identity authority created by an explicit operator command",
                metadata={"role_refs": ["IT_ADMIN"]},
            )
        )
        return {
            "actor_id": actor_id,
            "tenant_id": args.tenant_id,
            "organization_id": args.organization_id,
            "workspace_id": args.workspace_id,
            "correlation_id": correlation_id,
        }
    finally:
        await database.dispose()


def main() -> int:
    args = build_parser().parse_args()
    if args.command != "bootstrap-identity":
        raise SystemExit("unsupported command")
    password = getpass.getpass("Initial administrator password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("password confirmation does not match")
    try:
        result = asyncio.run(bootstrap_identity(args, password))
    except PlatformError as exc:
        raise SystemExit(f"{exc.code}: {exc.message}") from exc
    print(
        "Initial identity authority created: "
        f"actor={result['actor_id']} tenant={result['tenant_id']} "
        f"organization={result['organization_id']} workspace={result['workspace_id']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
