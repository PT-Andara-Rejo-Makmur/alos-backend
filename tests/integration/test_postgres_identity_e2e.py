"""Exercise employee provisioning and workspace authority against PostgreSQL."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import pytest
from sqlalchemy import select

from alos.config import Settings
from alos.main import create_app
from alos.persistence.models import EmployeeRecord


def _database_url(name: str) -> str:
    source = os.environ.get(
        "ALOS_TEST_DATABASE_URL",
        "postgresql+asyncpg://alos:alos@127.0.0.1:5432/alos_test",
    )
    parts = urlsplit(source)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


async def _recreate_database(name: str) -> str:
    url = _database_url(name)
    admin_parts = urlsplit(_database_url("postgres"))
    admin_url = urlunsplit(
        ("postgresql", admin_parts.netloc, "/postgres", admin_parts.query, admin_parts.fragment)
    )
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "upgrade",
        "head",
        env={**os.environ, "DATABASE_URL": url},
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode:
        raise RuntimeError(f"Alembic upgrade failed: {stdout!r} {stderr!r}")
    return url


@pytest.mark.asyncio
@pytest.mark.skipif(
    not os.environ.get("ALOS_TEST_DATABASE_URL"), reason="PostgreSQL test URL is required"
)
async def test_employee_provision_activation_and_workspace_lifecycle_on_postgres() -> None:
    url = await _recreate_database("alos_identity_e2e")
    settings = Settings(
        _env_file=None,
        APP_ENV="development",
        DATABASE_URL=url,
        GENESIS_BASE_URL="http://genesis.invalid",
        GENESIS_INTERNAL_TOKEN="test-only-token",  # noqa: S106
        OTEL_SERVICE_NAME="alos-identity-e2e",
        ENABLE_TEST_REGISTRATION=False,
    )
    app = create_app(settings)
    activation_tokens: dict[str, str] = {}
    app.state.auth_service._activation_sink = lambda email, token: activation_tokens.__setitem__(
        email, token
    )
    now = datetime.now(UTC)
    employee_id = "employee_identity_e2e"
    employee_email = "employee.identity.e2e@example.test"

    await app.state.auth_service.bootstrap_initial_admin(
        {
            "email": "identity.admin.e2e@example.test",
            "password": "StrongPass!123",
            "display_name": "Identity Administrator",
            "tenant_id": "tenant_identity_e2e",
            "organization_id": "org_identity_e2e",
            "workspace_id": "workspace_identity_it_e2e",
            "workspace_key": "identity-it-e2e",
            "workspace_name": "Identity IT",
            "workspace_type": "IT_OPERATIONS",
        }
    )
    async with app.state.database.session_factory() as session, session.begin():
        session.add(
            EmployeeRecord(
                employee_id=employee_id,
                tenant_id="tenant_identity_e2e",
                organization_id="org_identity_e2e",
                workspace_id="workspace_identity_it_e2e",
                actor_id=None,
                employee_number="E2E-001",
                full_name="Identity Employee",
                email=employee_email,
                employment_status="ACTIVE",
                join_date=date.today() - timedelta(days=30),
                end_date=None,
                department_code="IT",
                position_title="Systems Analyst",
                created_at=now,
                updated_at=now,
            )
        )

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            admin_login = await client.post(
                "/api/v1/auth/login",
                json={"email": "identity.admin.e2e@example.test", "password": "StrongPass!123"},
            )
            assert admin_login.status_code == 200
            admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}

            candidates = await client.get(
                "/api/v1/identity/provisioning-candidates", headers=admin_headers
            )
            assert candidates.status_code == 200
            assert [item["employee_id"] for item in candidates.json()] == [employee_id]

            provisioned = await client.post(
                "/api/v1/identity/accounts",
                headers=admin_headers,
                json={
                    "employee_id": employee_id,
                    "email": employee_email,
                    "workspace_id": "workspace_identity_it_e2e",
                    "role_refs": ["DIVISION_MEMBER"],
                    "effective_at": (now - timedelta(minutes=1)).isoformat(),
                },
            )
            assert provisioned.status_code == 201, provisioned.text
            actor_id = provisioned.json()["actor_id"]
            assert provisioned.json()["activation_state"] == "PENDING"
            async with app.state.database.session_factory() as session:
                employee = await session.scalar(
                    select(EmployeeRecord).where(EmployeeRecord.employee_id == employee_id)
                )
                assert employee is not None and employee.actor_id == actor_id

            pending_login = await client.post(
                "/api/v1/auth/login",
                json={"email": employee_email, "password": "NeverUsed!123"},
            )
            assert pending_login.status_code == 401
            activated = await client.post(
                "/api/v1/identity/activate",
                json={
                    "token": activation_tokens[employee_email],
                    "password": "EmployeePass!123",
                    "password_confirmation": "EmployeePass!123",
                },
            )
            assert activated.status_code == 200
            employee_login = await client.post(
                "/api/v1/auth/login",
                json={"email": employee_email, "password": "EmployeePass!123"},
            )
            assert employee_login.status_code == 200
            employee_headers = {"Authorization": f"Bearer {employee_login.json()['access_token']}"}
            workspaces = await client.get("/api/v1/workspaces", headers=employee_headers)
            assert [item["workspace"]["workspace_id"] for item in workspaces.json()] == [
                "workspace_identity_it_e2e"
            ]
            assert workspaces.json()[0]["role_refs"] == ["DIVISION_MEMBER"]

            async with app.state.database.session_factory() as session, session.begin():
                from alos.persistence.models import WorkspaceRecord

                session.add(
                    WorkspaceRecord(
                        workspace_id="workspace_identity_business_e2e",
                        tenant_id="tenant_identity_e2e",
                        organization_id="org_identity_e2e",
                        name="Identity Business",
                        workspace_key="identity-business-e2e",
                        workspace_type="BUSINESS",
                        organizational_unit_id=None,
                        division_code="FINANCE",
                        active=True,
                    )
                )
            mutation = {
                "workspace_id": "workspace_identity_business_e2e",
                "role_refs": ["DIVISION_LEAD"],
                "effective_at": (now - timedelta(minutes=1)).isoformat(),
            }
            added = await client.post(
                f"/api/v1/identity/actors/{actor_id}/memberships",
                headers=admin_headers,
                json=mutation,
            )
            assert added.status_code == 201, added.text
            workspaces = await client.get("/api/v1/workspaces", headers=employee_headers)
            assert [item["workspace"]["workspace_id"] for item in workspaces.json()] == [
                "workspace_identity_business_e2e",
                "workspace_identity_it_e2e",
            ]
            assert workspaces.json()[0]["role_refs"] == ["DIVISION_LEAD"]
            selected = await client.put(
                "/api/v1/auth/active-workspace",
                headers=employee_headers,
                json={"workspace_id": "workspace_identity_business_e2e"},
            )
            assert selected.status_code == 200
            assert selected.json()["membership"]["role_refs"] == ["DIVISION_LEAD"]

            revoked = await client.request(
                "DELETE",
                f"/api/v1/identity/actors/{actor_id}/memberships/workspace_identity_business_e2e",
                headers=admin_headers,
                json={"reason": "Integration lifecycle verification"},
            )
            assert revoked.status_code == 204
            workspaces = await client.get("/api/v1/workspaces", headers=employee_headers)
            assert [item["workspace"]["workspace_id"] for item in workspaces.json()] == [
                "workspace_identity_it_e2e"
            ]
            denied = await client.put(
                "/api/v1/auth/active-workspace",
                headers=employee_headers,
                json={"workspace_id": "workspace_identity_business_e2e"},
            )
            assert denied.status_code == 403
    finally:
        await app.state.database.dispose()
