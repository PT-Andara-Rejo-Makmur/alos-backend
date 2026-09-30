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

    import_emp = commands.add_parser(
        "import-employee",
        help="import initial employee master records (operator only)",
    )
    import_emp.add_argument("--employee-id")
    import_emp.add_argument("--employee-number")
    import_emp.add_argument("--full-name")
    import_emp.add_argument("--email")
    import_emp.add_argument("--tenant-id")
    import_emp.add_argument("--organization-id")
    import_emp.add_argument("--workspace-id")
    import_emp.add_argument("--department-code")
    import_emp.add_argument("--position-title")
    import_emp.add_argument("--employment-status", default="ACTIVE")
    import_emp.add_argument("--join-date")
    import_emp.add_argument("--end-date")
    import_emp.add_argument(
        "--file", help="Path to JSON file containing employee or list of employees"
    )
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


def _load_employee_items(args: argparse.Namespace) -> list[dict[str, object]]:
    import json
    from pathlib import Path

    items: list[dict[str, object]] = []
    if args.file:
        file_path = Path(args.file)
        if not file_path.is_file():
            raise PlatformError(
                "FILE_NOT_FOUND", f"employee import file not found: {args.file}", status_code=400
            )
        content = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(content, list):
            items.extend(content)
        elif isinstance(content, dict):
            items.append(content)
        else:
            raise PlatformError(
                "INVALID_FILE_FORMAT", "expected a JSON object or array of objects", status_code=400
            )
    else:
        if not all(
            (
                args.employee_id,
                args.employee_number,
                args.full_name,
                args.tenant_id,
                args.organization_id,
                args.workspace_id,
            )
        ):
            raise PlatformError(
                "MISSING_REQUIRED_FIELDS",
                (
                    "employee-id, employee-number, full-name, tenant-id, organization-id, "
                    "and workspace-id are required"
                ),
                status_code=400,
            )
        items.append(
            {
                "employee_id": args.employee_id,
                "employee_number": args.employee_number,
                "full_name": args.full_name,
                "email": args.email,
                "tenant_id": args.tenant_id,
                "organization_id": args.organization_id,
                "workspace_id": args.workspace_id,
                "department_code": args.department_code,
                "position_title": args.position_title,
                "employment_status": args.employment_status,
                "join_date": args.join_date,
                "end_date": args.end_date,
            }
        )
    return items


async def run_import_employee(items: list[dict[str, object]]) -> list[dict[str, object]]:
    settings = Settings()
    database = Database(settings.DATABASE_URL)
    service = AuthService(SqlAuthRepository(database.session_factory))
    audit = SqlAuditRepository(database.session_factory)
    results = []
    try:
        for item in items:
            correlation_id = f"employee_import_{uuid.uuid4().hex}"
            res = await service.import_employee(item)
            await audit.append(
                AuditEvent(
                    event_type="identity.employee.imported",
                    entity_type="employee",
                    entity_id=str(res["employee_id"]),
                    tenant_id=str(res["tenant_id"]),
                    organization_id=str(res["organization_id"]),
                    workspace_id=str(res["workspace_id"]),
                    actor_id="OPERATOR",
                    actor_kind="SYSTEM",
                    correlation_id=correlation_id,
                    outcome="SUCCEEDED",
                    occurred_at=datetime.now(UTC),
                    reason="Initial employee master record imported via operator CLI",
                    metadata={"employee_number": res["employee_number"]},
                )
            )
            results.append(res)
        return results
    finally:
        await database.dispose()


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "bootstrap-identity":
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
    elif args.command == "import-employee":
        try:
            items = _load_employee_items(args)
            results = asyncio.run(run_import_employee(items))
        except PlatformError as exc:
            raise SystemExit(f"{exc.code}: {exc.message}") from exc
        print(f"Successfully imported {len(results)} employee(s):")
        for emp in results:
            id_no = f"{emp['employee_id']} ({emp['employee_number']})"
            print(f" - {id_no}: {emp['full_name']} <{emp['email']}>")
        return 0
    else:
        raise SystemExit("unsupported command")


if __name__ == "__main__":
    raise SystemExit(main())
