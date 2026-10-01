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
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from alos.cli import bootstrap_identity, build_parser
from alos.config import Settings
from alos.main import create_app
from alos.persistence.models import AuditRecord, AuthSessionRecord, EmployeeRecord
from alos.security.errors import PlatformError


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
async def test_employee_provision_activation_and_workspace_lifecycle_on_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    monkeypatch.setattr("alos.cli.Settings", lambda: settings)
    bootstrap_args = build_parser().parse_args(
        [
            "bootstrap-identity",
            "--email",
            "identity.admin.e2e@example.test",
            "--display-name",
            "Identity Administrator",
            "--tenant-id",
            "tenant_identity_e2e",
            "--organization-id",
            "org_identity_e2e",
            "--workspace-id",
            "workspace_identity_it_e2e",
            "--workspace-key",
            "identity-it-e2e",
            "--workspace-name",
            "Identity IT",
            "--workspace-type",
            "IT_OPERATIONS",
        ]
    )
    initial = await bootstrap_identity(bootstrap_args, "StrongPass!123")
    async with app.state.database.session_factory() as session:
        bootstrap_audits = list(
            await session.scalars(
                select(AuditRecord).where(
                    AuditRecord.event_type == "identity.initial_authority.bootstrapped"
                )
            )
        )
        assert len(bootstrap_audits) == 1
        assert bootstrap_audits[0].entity_id == initial["actor_id"]
        assert bootstrap_audits[0].actor_kind == "SYSTEM"
        assert bootstrap_audits[0].correlation_id == initial["correlation_id"]
        assert bootstrap_audits[0].event_metadata == {"role_refs": ["IT_ADMIN"]}
    with pytest.raises(PlatformError) as repeated_bootstrap:
        await bootstrap_identity(bootstrap_args, "StrongPass!123")
    assert repeated_bootstrap.value.code == "IDENTITY_BOOTSTRAP_EXISTS"
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
                email="  Employee.Identity.E2E@Example.Test  ",
                employment_status="ACTIVE",
                join_date=date.today() - timedelta(days=30),
                end_date=None,
                department_code="IT",
                position_title="Systems Analyst",
                created_at=now,
                updated_at=now,
            )
        )

    async with app.state.database.session_factory() as session, session.begin():
        for suffix, email, joined, ended in (
            ("missing_email", None, now.date(), None),
            ("invalid_email", "invalid@@example.com", now.date(), None),
            ("missing_join", "missing-join@example.test", None, None),
            ("future_join", "future-join@example.test", now.date() + timedelta(days=1), None),
            ("ended", "ended@example.test", now.date(), now.date() - timedelta(days=1)),
        ):
            session.add(
                EmployeeRecord(
                    employee_id=f"employee_legacy_{suffix}",
                    employee_number=f"LEGACY-{suffix}",
                    full_name="Legacy Employee",
                    tenant_id="tenant_identity_e2e",
                    organization_id="org_identity_e2e",
                    workspace_id="workspace_identity_it_e2e",
                    email=email,
                    join_date=joined,
                    end_date=ended,
                    employment_status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
            )
    with pytest.raises(PlatformError) as duplicate:
        await app.state.auth_service.import_employee(
            {
                "employee_id": "employee_duplicate",
                "employee_number": "DUP-001",
                "full_name": "Duplicate",
                "email": employee_email,
                "tenant_id": "tenant_identity_e2e",
                "organization_id": "org_identity_e2e",
                "workspace_id": "workspace_identity_it_e2e",
                "join_date": now.date(),
            }
        )
    assert duplicate.value.code == "EMPLOYEE_CONFLICT"

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
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
            assert candidates.json()[0]["email"] == employee_email
            rejected = await client.post(
                "/api/v1/identity/accounts",
                headers=admin_headers,
                json={
                    "employee_id": employee_id,
                    "email": "override@example.test",
                    "workspace_id": "workspace_identity_it_e2e",
                    "role_refs": ["DIVISION_MEMBER"],
                    "effective_at": now.isoformat(),
                },
            )
            assert rejected.status_code == 422
            for suffix in (
                "missing_email",
                "invalid_email",
                "missing_join",
                "future_join",
                "ended",
            ):
                rejected = await client.post(
                    "/api/v1/identity/accounts",
                    headers=admin_headers,
                    json={
                        "employee_id": f"employee_legacy_{suffix}",
                        "workspace_id": "workspace_identity_it_e2e",
                        "role_refs": ["DIVISION_MEMBER"],
                        "effective_at": now.isoformat(),
                    },
                )
                assert rejected.status_code == 409

            provisioned = await client.post(
                "/api/v1/identity/accounts",
                headers=admin_headers,
                json={
                    "employee_id": employee_id,
                    "workspace_id": "workspace_identity_it_e2e",
                    "role_refs": ["DIVISION_MEMBER"],
                    "effective_at": (now - timedelta(minutes=1)).isoformat(),
                },
            )
            assert provisioned.status_code == 201, provisioned.text
            assert provisioned.json()["email"] == employee_email
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
            assert activated.json() == {
                "actor_id": actor_id,
                "activation_state": "ACTIVATED",
            }
            reused = await client.post(
                "/api/v1/identity/activate",
                json={
                    "token": activation_tokens[employee_email],
                    "password": "EmployeePass!123",
                    "password_confirmation": "EmployeePass!123",
                },
            )
            assert reused.status_code == 422
            assert reused.json()["code"] == "ACTIVATION_CHALLENGE_INVALID"
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

            # Specific-session revocation must commit, invalidate the token and retain history.
            sessions_path = f"/api/v1/identity/actors/{actor_id}/sessions"
            sessions = await client.get(sessions_path, headers=admin_headers)
            assert sessions.status_code == 200
            assert len(sessions.json()) == 1
            session_id = sessions.json()[0]["session_id"]
            revoke_path = f"{sessions_path}/{session_id}"
            assert not sessions.json()[0]["revoked"]
            assert (
                await client.get("/api/v1/auth/whoami", headers=employee_headers)
            ).status_code == 200
            async with app.state.database.session_factory() as session:
                record = await session.get(AuthSessionRecord, session_id)
                assert record is not None and record.active and record.revoked_at is None
                assert record.token_hash != employee_login.json()["access_token"]

            for tenant_id, organization_id in (
                ("foreign_tenant", "org_identity_e2e"),
                ("tenant_identity_e2e", "foreign_org"),
            ):
                with pytest.raises(PlatformError) as boundary:
                    await app.state.auth_service.revoke_actor_session(
                        actor_id, session_id, tenant_id=tenant_id, organization_id=organization_id
                    )
                assert boundary.value.code == "SESSION_NOT_FOUND"
            admin_principal = (
                await client.get("/api/v1/auth/whoami", headers=admin_headers)
            ).json()
            admin_actor_id = admin_principal["actor"]["actor_id"]
            cross_actor = await client.delete(
                f"/api/v1/identity/actors/{admin_actor_id}/sessions/{session_id}",
                headers=admin_headers,
            )
            assert cross_actor.status_code == 404
            # Non-admin employees cannot revoke another actor's session either.
            admin_sessions = await client.get(
                f"/api/v1/identity/actors/{admin_actor_id}/sessions", headers=admin_headers
            )
            forbidden = await client.delete(
                f"/api/v1/identity/actors/{admin_actor_id}/sessions/"
                f"{admin_sessions.json()[0]['session_id']}",
                headers=employee_headers,
            )
            assert forbidden.status_code == 403

            revoke_commit_attempted = False

            def fail_session_commit(transaction: Session) -> None:
                nonlocal revoke_commit_attempted
                if any(
                    isinstance(row, AuthSessionRecord)
                    and row.session_id == session_id
                    and not row.active
                    and row.revoked_at is not None
                    for row in transaction.dirty
                ):
                    transaction.flush()
                    revoke_commit_attempted = True
                    raise RuntimeError("Simulated PostgreSQL commit failure")

            event.listen(Session, "before_commit", fail_session_commit)
            try:
                failed = await client.delete(revoke_path, headers=admin_headers)
                assert failed.status_code == 500
                assert revoke_commit_attempted
            finally:
                event.remove(Session, "before_commit", fail_session_commit)
            async with app.state.database.session_factory() as session:
                record = await session.get(AuthSessionRecord, session_id)
                assert record is not None and record.active and record.revoked_at is None
                assert not list(
                    await session.scalars(
                        select(AuditRecord).where(AuditRecord.event_type == "auth.session.revoked")
                    )
                )
            assert (
                await client.get("/api/v1/auth/whoami", headers=employee_headers)
            ).status_code == 200

            revoked = await client.delete(revoke_path, headers=admin_headers)
            assert revoked.status_code == 204
            async with app.state.database.session_factory() as session:
                record = await session.get(AuthSessionRecord, session_id)
                assert record is not None and not record.active and record.revoked_at is not None
                revoked_at = record.revoked_at
                audits = list(
                    await session.scalars(
                        select(AuditRecord).where(AuditRecord.event_type == "auth.session.revoked")
                    )
                )
                assert len(audits) == 1
                assert audits[0].event_metadata == {"session_id": session_id}
            assert (
                await client.get("/api/v1/auth/whoami", headers=employee_headers)
            ).status_code == 401
            sessions = await client.get(sessions_path, headers=admin_headers)
            assert sessions.json()[0]["session_id"] == session_id
            assert sessions.json()[0]["revoked"] is True
            repeated = await client.delete(revoke_path, headers=admin_headers)
            assert repeated.status_code == 404
            async with app.state.database.session_factory() as session:
                record = await session.get(AuthSessionRecord, session_id)
                assert record is not None and not record.active and record.revoked_at == revoked_at
                assert (
                    len(
                        list(
                            await session.scalars(
                                select(AuditRecord).where(
                                    AuditRecord.event_type == "auth.session.revoked"
                                )
                            )
                        )
                    )
                    == 1
                )
    finally:
        await app.state.database.dispose()
