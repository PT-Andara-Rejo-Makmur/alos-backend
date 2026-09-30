"""Finding lifecycle authority against scoped PostgreSQL records."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import pytest

from alos.config import Settings
from alos.main import create_app

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_ROOT = BACKEND_ROOT.parent / "alos-contracts"


def _database_url(name: str) -> str:
    source = os.environ.get(
        "ALOS_TEST_DATABASE_URL", "postgresql+asyncpg://alos:alos@127.0.0.1:5432/alos_test"
    )
    parts = urlsplit(source)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


async def _database(name: str, *, create: bool) -> None:
    admin = await asyncpg.connect(_database_url("postgres").replace("+asyncpg", ""))
    try:
        if create:
            await admin.execute(f'CREATE DATABASE "{name}"')
        else:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _login(
    client: httpx.AsyncClient,
    *,
    email: str,
    permissions: list[str],
    tenant_id: str = "tenant_default",
    organization_id: str = "org_default",
    workspace_id: str = "workspace_property",
    workspace_key: str = "property",
) -> tuple[dict[str, str], str]:
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass!123",
            "display_name": "Finding Test",
            "tenant_id": tenant_id,
            "organization_id": organization_id,
            "workspace_id": workspace_id,
            "workspace_key": workspace_key,
            "workspace_name": "Test Workspace",
            "workspace_type": "BUSINESS",
            "division_code": "UNASSIGNED",
            "role_refs": ["DIVISION_MEMBER"],
            "permission_refs": permissions,
            "scope_refs": [],
            "data_scope": "WORKSPACE",
        },
    )
    assert registered.status_code == 201, registered.text
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "StrongPass!123"}
    )
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, registered.json()[
        "actor_id"
    ]


async def _target(
    postgres: asyncpg.Connection,
    actor_id: str,
    *,
    tenant: str = "tenant_default",
    organization: str = "org_default",
    workspace: str = "workspace_property",
    active: bool = True,
    member_active: bool = True,
    revoked: bool = False,
    expired: bool = False,
    permissions: str = '["finding.read"]',
) -> None:
    await postgres.execute(
        "INSERT INTO core.actors (actor_id,tenant_id,organization_id,display_name,active) "
        "VALUES ($1,$2,$3,$1,$4)",
        actor_id,
        tenant,
        organization,
        active,
    )
    await postgres.execute(
        "INSERT INTO core.workspace_memberships "
        "(actor_id,workspace_id,tenant_id,organization_id,roles,permission_refs,"
        "scope_refs,data_scope,active,revoked_at,created_at,effective_at,expires_at,updated_at) "
        "VALUES ($1,$2,$3,$4,'[\"DIVISION_MEMBER\"]',$5::json,'[]','WORKSPACE',$6,"
        "CASE WHEN $7 THEN now() ELSE NULL END,now(),now(),"
        "CASE WHEN $8 THEN now() - interval '1 day' ELSE NULL END,now())",
        actor_id,
        workspace,
        tenant,
        organization,
        permissions,
        member_active,
        revoked,
        expired,
    )


@pytest.mark.asyncio
async def test_finding_lifecycle_authority_and_audit() -> None:
    name = f"alos_finding_lifecycle_{uuid.uuid4().hex[:8]}"
    await _database(name, create=True)
    database_url = _database_url(name)
    app = None
    try:
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": database_url},
            check=True,
            capture_output=True,
            text=True,
        )
        app = create_app(
            Settings(
                _env_file=None,
                APP_ENV="test",
                DATABASE_URL=database_url,
                ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
                ENABLE_TEST_REGISTRATION=True,
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            owner, owner_id = await _login(
                client,
                email="finding-owner@andara.local",
                permissions=["finding.read", "finding.create", "finding.update", "finding.verify"],
            )
            manager, _ = await _login(
                client,
                email="finding-manager@andara.local",
                permissions=["finding.read", "finding.assign", "finding.update"],
            )
            verifier, _ = await _login(
                client,
                email="finding-verifier@andara.local",
                permissions=["finding.read", "finding.verify"],
            )
            closer, _ = await _login(
                client,
                email="finding-closer@andara.local",
                permissions=["finding.read", "finding.close"],
            )
            legacy, _ = await _login(
                client,
                email="finding-legacy@andara.local",
                permissions=["work.read", "work.write", "work.delete"],
            )
            remote, _ = await _login(
                client,
                email="finding-remote@andara.local",
                permissions=[
                    "finding.read",
                    "finding.update",
                    "finding.assign",
                    "finding.verify",
                    "finding.close",
                ],
                workspace_id="workspace_hr",
                workspace_key="hr",
            )
            await _login(
                client,
                email="finding-other-org@andara.local",
                permissions=["finding.read"],
                organization_id="org_other",
                workspace_id="workspace_other_org",
                workspace_key="other_org",
            )
            await _login(
                client,
                email="finding-other-tenant@andara.local",
                permissions=["finding.read"],
                tenant_id="tenant_other",
                organization_id="org_other_tenant",
                workspace_id="workspace_other_tenant",
                workspace_key="other_tenant",
            )
            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                await postgres.execute(
                    "INSERT INTO core.tenants (tenant_id,name) VALUES "
                    "('tenant_other','Other Tenant') ON CONFLICT DO NOTHING"
                )
                await postgres.execute(
                    "INSERT INTO core.organizations (organization_id,tenant_id,name) VALUES "
                    "('org_other','tenant_default','Other Organization'),"
                    "('org_other_tenant','tenant_other','Other Tenant Organization') "
                    "ON CONFLICT DO NOTHING"
                )
                await postgres.execute(
                    "INSERT INTO core.workspaces "
                    "(workspace_id,tenant_id,organization_id,name,workspace_key,"
                    "workspace_type,division_code,active) VALUES "
                    "('workspace_other_org','tenant_default','org_other','Other Org',"
                    "'other_org','BUSINESS','UNASSIGNED',true),"
                    "('workspace_other_tenant','tenant_other','org_other_tenant','Other Tenant',"
                    "'other_tenant','BUSINESS','UNASSIGNED',true) ON CONFLICT DO NOTHING"
                )
                await _target(postgres, owner_id)
                for record_id, tenant, organization in (
                    ("finding_foreign_tenant", "tenant_other", "org_other_tenant"),
                    ("finding_foreign_org", "tenant_default", "org_other"),
                ):
                    await postgres.execute(
                        "INSERT INTO core.work_findings "
                        "(finding_id,tenant_id,organization_id,title,severity,status,"
                        "source_type,owner_actor_id,created_at,updated_at) "
                        "VALUES ($1,$2,$3,'Foreign finding','HIGH','OPEN','MANUAL',"
                        "'actor_foreign',now(),now())",
                        record_id,
                        tenant,
                        organization,
                    )
                    await postgres.execute(
                        "INSERT INTO core.work_finding_workspaces (finding_id,workspace_id) "
                        "VALUES ($1,'workspace_property')",
                        record_id,
                    )
                for actor, kwargs in (
                    ("target_valid", {}),
                    ("target_second", {"permissions": '["work.read"]'}),
                    (
                        "target_tenant",
                        {
                            "tenant": "tenant_other",
                            "organization": "org_other_tenant",
                            "workspace": "workspace_other_tenant",
                        },
                    ),
                    (
                        "target_org",
                        {"organization": "org_other", "workspace": "workspace_other_org"},
                    ),
                    ("target_workspace", {"workspace": "workspace_hr"}),
                    ("target_inactive", {"active": False}),
                    ("target_member_inactive", {"member_active": False}),
                    ("target_revoked", {"revoked": True}),
                    ("target_expired", {"expired": True}),
                    ("target_unreadable", {"permissions": "[]"}),
                ):
                    await _target(postgres, actor, **kwargs)
            finally:
                await postgres.close()

            created = await client.post(
                "/api/v1/work/findings",
                headers=owner,
                json={"title": "Initial finding", "severity": "HIGH"},
            )
            assert created.status_code == 201, created.text
            finding_id = created.json()["finding_id"]
            base = f"/api/v1/work/findings/{finding_id}"
            assert created.json()["owner_actor_id"] == owner_id
            for foreign_id in ("finding_foreign_tenant", "finding_foreign_org"):
                foreign = f"/api/v1/work/findings/{foreign_id}"
                assert (
                    await client.patch(foreign, headers=owner, json={"title": "No"})
                ).status_code == 404
                assert (
                    await client.post(
                        foreign + "/assign",
                        headers=manager,
                        json={"owner_actor_id": "target_valid"},
                    )
                ).status_code == 404
                assert (await client.post(foreign + "/verify", headers=verifier)).status_code == 404
                assert (await client.post(foreign + "/close", headers=closer)).status_code == 404
            assert (await client.patch(base, json={"title": "Denied"})).status_code == 401
            assert (
                await client.patch(base, headers=legacy, json={"title": "Denied"})
            ).status_code == 403
            assert (
                await client.post(
                    base + "/assign", headers=legacy, json={"owner_actor_id": "target_valid"}
                )
            ).status_code == 403
            for field in (
                "status",
                "owner_actor_id",
                "source_type",
                "tenant_id",
                "organization_id",
                "workspace_ids",
            ):
                assert (
                    await client.patch(base, headers=owner, json={field: "forged"})
                ).status_code == 422
            edited = await client.patch(
                base,
                headers=owner,
                json={"title": "Updated finding", "description": "Reviewed", "severity": "LOW"},
            )
            assert edited.status_code == 200, edited.text
            assert edited.json()["status"] == "OPEN"
            assert edited.json()["owner_actor_id"] == owner_id
            assert (await client.post(base + "/verify", headers=verifier)).status_code == 409
            assert (await client.post(base + "/close", headers=closer)).status_code == 409
            assert (
                await client.patch(base, headers=remote, json={"title": "Remote"})
            ).status_code == 404
            assert (
                await client.post(
                    base + "/assign", headers=remote, json={"owner_actor_id": "target_valid"}
                )
            ).status_code == 404
            for target in (
                "target_tenant",
                "target_org",
                "target_workspace",
                "target_inactive",
                "target_member_inactive",
                "target_revoked",
                "target_expired",
                "target_unreadable",
            ):
                response = await client.post(
                    base + "/assign", headers=manager, json={"owner_actor_id": target}
                )
                assert response.status_code == 404, (target, response.text)
            assert (
                await client.post(
                    base + "/assign",
                    headers=manager,
                    json={"owner_actor_id": "target_valid", "status": "CLOSED"},
                )
            ).status_code == 422
            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                await postgres.execute(
                    "UPDATE core.workspaces SET active=false "
                    "WHERE workspace_id='workspace_property'"
                )
                assert (
                    await client.post(
                        base + "/assign",
                        headers=manager,
                        json={"owner_actor_id": "target_valid"},
                    )
                ).status_code == 404
            finally:
                await postgres.execute(
                    "UPDATE core.workspaces SET active=true WHERE workspace_id='workspace_property'"
                )
                await postgres.close()
            assigned = await client.post(
                base + "/assign", headers=manager, json={"owner_actor_id": "target_valid"}
            )
            assert assigned.status_code == 200, assigned.text
            assert assigned.json()["status"] == "ASSIGNED"
            assert assigned.json()["owner_actor_id"] == "target_valid"
            assigned_edit = await client.patch(
                base, headers=manager, json={"description": "Assigned edit"}
            )
            assert assigned_edit.status_code == 200
            retry = await client.post(
                base + "/assign", headers=manager, json={"owner_actor_id": "target_valid"}
            )
            assert retry.status_code == 200
            assert retry.json()["updated_at"] == assigned_edit.json()["updated_at"]
            reassigned = await client.post(
                base + "/assign", headers=manager, json={"owner_actor_id": "target_second"}
            )
            assert reassigned.status_code == 200
            assert reassigned.json()["owner_actor_id"] == "target_second"
            assert (await client.post(base + "/start", headers=owner)).status_code == 403
            # Return ownership to a registered, eligible actor and start from ASSIGNED.
            assert (
                await client.post(
                    base + "/assign", headers=manager, json={"owner_actor_id": owner_id}
                )
            ).status_code == 200
            started_assigned = await client.post(base + "/start", headers=owner)
            assert started_assigned.status_code == 200
            assert started_assigned.json()["status"] == "IN_PROGRESS"
            assert (
                await client.patch(base, headers=owner, json={"description": "Work edit"})
            ).status_code == 200
            second = await client.post(
                "/api/v1/work/findings",
                headers=owner,
                json={"title": "Owner workflow"},
            )
            assert second.status_code == 201, second.text
            workflow_id = second.json()["finding_id"]
            workflow = f"/api/v1/work/findings/{workflow_id}"
            assert (await client.post(workflow + "/start", headers=manager)).status_code == 403
            started = await client.post(workflow + "/start", headers=owner)
            assert started.status_code == 200
            assert started.json()["status"] == "IN_PROGRESS"
            assert (await client.post(workflow + "/start", headers=owner)).json()[
                "updated_at"
            ] == started.json()["updated_at"]
            assert (
                await client.post(
                    workflow + "/assign", headers=manager, json={"owner_actor_id": "target_valid"}
                )
            ).status_code == 409
            assert (
                await client.post(workflow + "/submit-verification", headers=manager)
            ).status_code == 403
            submitted = await client.post(workflow + "/submit-verification", headers=owner)
            assert submitted.status_code == 200
            assert submitted.json()["status"] == "PENDING_VERIFICATION"
            assert (await client.post(workflow + "/submit-verification", headers=owner)).json()[
                "updated_at"
            ] == submitted.json()["updated_at"]
            assert (
                await client.patch(workflow, headers=owner, json={"title": "Too late"})
            ).status_code == 409
            assert (await client.post(workflow + "/close", headers=closer)).status_code == 409
            assert (await client.post(workflow + "/verify", headers=owner)).status_code == 403
            assert (await client.post(workflow + "/verify", headers=closer)).status_code == 403
            verified = await client.post(workflow + "/verify", headers=verifier)
            assert verified.status_code == 200
            assert verified.json()["status"] == "VERIFIED"
            assert (await client.post(workflow + "/verify", headers=verifier)).json()[
                "updated_at"
            ] == verified.json()["updated_at"]
            assert (await client.post(workflow + "/start", headers=owner)).status_code == 409
            closed = await client.post(workflow + "/close", headers=closer)
            assert closed.status_code == 200
            assert closed.json()["status"] == "CLOSED"
            assert (await client.post(workflow + "/close", headers=closer)).json()[
                "updated_at"
            ] == closed.json()["updated_at"]
            assert (
                await client.patch(workflow, headers=owner, json={"title": "Too late"})
            ).status_code == 409
            assert (await client.post(workflow + "/verify", headers=verifier)).status_code == 409
            for method in ("post", "patch", "put", "delete"):
                response = await client.request(
                    method.upper(),
                    f"/api/v1/domains/shared/work_findings/{workflow_id}",
                    headers=legacy,
                    json={"status": "OPEN"} if method != "delete" else None,
                )
                assert response.status_code in {403, 405, 409}, response.text
            events = app.state.identity_audit.list_events(tenant_id="tenant_default")
            for event_type, count in (
                ("finding.updated", 3),
                ("finding.assigned", 3),
                ("finding.started", 2),
                ("finding.verification_requested", 1),
                ("finding.verified", 1),
                ("finding.closed", 1),
            ):
                assert sum(e.event_type == event_type for e in events) == count, event_type
    finally:
        if app is not None:
            await app.state.database.dispose()
        await _database(name, create=False)
