"""Projects and Tasks use PostgreSQL and the active session authority."""

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

from alos.authentication.repository import SqlAuthRepository
from alos.authentication.service import AuthService
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


async def _create_database(name: str) -> None:
    admin = await asyncpg.connect(_database_url("postgres").replace("+asyncpg", ""))
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()


async def _drop_database(name: str) -> None:
    admin = await asyncpg.connect(_database_url("postgres").replace("+asyncpg", ""))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _login(
    client: httpx.AsyncClient,
    *,
    email: str,
    permissions: list[str],
    workspace_id: str = "workspace_property",
    workspace_key: str = "property",
) -> dict[str, str]:
    # The work records validate owners against current persisted memberships.
    app = client._transport.app
    if not isinstance(app.state.auth_service._repository, SqlAuthRepository):
        app.state.auth_service = AuthService(
            SqlAuthRepository(app.state.database.session_factory),
            session_ttl_minutes=app.state.settings.AUTH_SESSION_TTL_MINUTES,
            notification_service=app.state.notification_service,
        )
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass!123",
            "display_name": "Shared Work Test",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": workspace_id,
            "workspace_key": workspace_key,
            "workspace_name": "Property Workspace",
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
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


@pytest.mark.asyncio
async def test_projects_and_tasks_are_workspace_scoped_and_permission_bounded() -> None:
    database_name = f"alos_work_{uuid.uuid4().hex[:12]}"
    await _create_database(database_name)
    database_url = _database_url(database_name)
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
                GENESIS_BASE_URL="http://genesis.test",
                GENESIS_INTERNAL_TOKEN="shared-work-test-token",  # noqa: S106
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in ("/api/v1/projects", "/api/v1/tasks"):
                assert (await client.get(path)).status_code == 401
                assert (await client.post(path, json={})).status_code == 401

            denied = await _login(client, email="work-denied@andara.local", permissions=[])
            for path in ("/api/v1/projects", "/api/v1/tasks"):
                assert (await client.get(path, headers=denied)).status_code == 403
                assert (await client.post(path, headers=denied, json={})).status_code == 403

            legacy = await _login(
                client,
                email="work-legacy@andara.local",
                permissions=["work.read", "work.write", "work.delete"],
            )
            project = await client.post(
                "/api/v1/projects",
                headers=legacy,
                json={"code": "PROJECT-1", "name": "Project One"},
            )
            assert project.status_code == 201, project.text
            project_data = project.json()
            project_id = project_data["project_id"]
            assert project_data["status"] == "PLANNED"
            assert project_data["workspace_ids"] == ["workspace_property"]
            assert project_data["tenant_id"] == "tenant_default"
            assert project_data["organization_id"] == "org_default"
            assert project_data["owner_actor_id"]

            task = await client.post(
                "/api/v1/tasks",
                headers=legacy,
                json={"title": "Task One", "project_id": project_id, "priority": "HIGH"},
            )
            assert task.status_code == 201, task.text
            task_data = task.json()
            assert project_data["owner_actor_id"] == task_data["created_by"]
            task_id = task_data["task_id"]
            assert task_data["status"] == "OPEN"
            assert task_data["priority"] == "HIGH"
            assert task_data["workspace_ids"] == ["workspace_property"]
            assert task_data["project_id"] == project_id
            assert task_data["created_by"] != ""

            for collection, identifier in (("projects", project_id), ("tasks", task_id)):
                listed = await client.get(f"/api/v1/{collection}", headers=legacy)
                detail = await client.get(f"/api/v1/{collection}/{identifier}", headers=legacy)
                assert listed.status_code == 200 and len(listed.json()) == 1
                assert detail.status_code == 200
                assert detail.json() == listed.json()[0]
                assert identifier in str(detail.json())

            assert (
                len((await client.get("/api/v1/projects?status=PLANNED", headers=legacy)).json())
                == 1
            )
            assert (
                await client.get("/api/v1/projects?search=missing", headers=legacy)
            ).json() == []
            assert (
                len((await client.get("/api/v1/tasks?priority=HIGH", headers=legacy)).json()) == 1
            )
            assert (await client.get("/api/v1/tasks?status=COMPLETED", headers=legacy)).json() == []

            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                stored_project = await postgres.fetchrow(
                    "SELECT tenant_id, organization_id, status "
                    "FROM core.projects WHERE project_id=$1",
                    project_id,
                )
                stored_task = await postgres.fetchrow(
                    "SELECT tenant_id, organization_id, created_by, status "
                    "FROM core.tasks WHERE task_id=$1",
                    task_id,
                )
                assert stored_project["status"] == "PLANNED"
                assert stored_task["status"] == "OPEN"
                assert stored_task["created_by"] == task_data["created_by"]
                assert stored_project["tenant_id"] == stored_task["tenant_id"] == "tenant_default"
                assert (
                    stored_project["organization_id"]
                    == stored_task["organization_id"]
                    == "org_default"
                )
                assert (
                    await postgres.fetchval(
                        "SELECT workspace_id FROM core.project_workspaces WHERE project_id=$1",
                        project_id,
                    )
                    == "workspace_property"
                )
                assert (
                    await postgres.fetchval(
                        "SELECT workspace_id FROM core.task_workspaces WHERE task_id=$1", task_id
                    )
                    == "workspace_property"
                )

                for identifier, tenant, organization, workspace in (
                    ("project.other.workspace", "tenant_default", "org_default", "workspace_hr"),
                    ("project.other.org", "tenant_default", "org_other", "workspace_property"),
                    ("project.other.tenant", "tenant_other", "org_default", "workspace_property"),
                ):
                    await postgres.execute(
                        "INSERT INTO core.projects "
                        "(project_id,tenant_id,organization_id,code,name,status,"
                        "created_at,updated_at) "
                        "VALUES ($1,$2,$3,$1,$1,'PLANNED',now(),now())",
                        identifier,
                        tenant,
                        organization,
                    )
                    await postgres.execute(
                        "INSERT INTO core.project_workspaces "
                        "(project_id,workspace_id) VALUES ($1,$2)",
                        identifier,
                        workspace,
                    )
                for identifier, tenant, organization, workspace in (
                    ("task.other.workspace", "tenant_default", "org_default", "workspace_hr"),
                    ("task.other.org", "tenant_default", "org_other", "workspace_property"),
                    ("task.other.tenant", "tenant_other", "org_default", "workspace_property"),
                ):
                    await postgres.execute(
                        "INSERT INTO core.tasks "
                        "(task_id,tenant_id,organization_id,title,status,priority,"
                        "created_by,created_at,updated_at) "
                        "VALUES ($1,$2,$3,$1,'OPEN','NORMAL','actor.other',now(),now())",
                        identifier,
                        tenant,
                        organization,
                    )
                    await postgres.execute(
                        "INSERT INTO core.task_workspaces (task_id,workspace_id) VALUES ($1,$2)",
                        identifier,
                        workspace,
                    )
            finally:
                await postgres.close()

            for hidden in ("project.other.workspace", "project.other.org", "project.other.tenant"):
                assert (
                    await client.get(f"/api/v1/projects/{hidden}", headers=legacy)
                ).status_code == 404
                response = await client.post(
                    "/api/v1/tasks",
                    headers=legacy,
                    json={"title": "Hidden ref", "project_id": hidden},
                )
                assert response.status_code == 404
            for hidden in ("task.other.workspace", "task.other.org", "task.other.tenant"):
                assert (
                    await client.get(f"/api/v1/tasks/{hidden}", headers=legacy)
                ).status_code == 404
            assert (
                len((await client.get("/api/v1/projects?workspace_key=hr", headers=legacy)).json())
                == 1
            )
            assert (
                len((await client.get("/api/v1/tasks?workspace_key=hr", headers=legacy)).json())
                == 1
            )

            for path, payload in (
                ("projects", {"code": "FORGED", "name": "Forged", "tenant_id": "tenant_other"}),
                ("projects", {"code": "FORGED", "name": "Forged", "status": "ACTIVE"}),
                ("tasks", {"title": "Forged", "created_by": "actor.other"}),
                ("tasks", {"title": "Forged", "workspace_ids": ["workspace_hr"]}),
            ):
                response = await client.post(f"/api/v1/{path}", headers=legacy, json=payload)
                assert response.status_code == 422
                assert response.json()["code"] == "WORK_CONTRACT_INVALID"

            for collection, payload, identifier in (
                ("projects", {"code": "BYPASS", "name": "Bypass"}, project_id),
                ("tasks", {"title": "Bypass"}, task_id),
            ):
                path = f"/api/v1/domains/shared/{collection}"
                for method, url, body in (
                    ("POST", path, payload),
                    ("PATCH", f"{path}/{identifier}", {"status": "COMPLETED"}),
                    ("DELETE", f"{path}/{identifier}", None),
                ):
                    response = await client.request(method, url, headers=legacy, json=body)
                    assert response.status_code == 409, response.text
                    assert response.json()["code"] == "WORK_MUTATION_REQUIRES_DEDICATED_API"

            project_only = await _login(
                client, email="work-project-only@andara.local", permissions=["project.create"]
            )
            assert (await client.get("/api/v1/projects", headers=project_only)).status_code == 403
            assert (
                await client.post("/api/v1/tasks", headers=project_only, json={"title": "No"})
            ).status_code == 403
            assert (
                await client.post(
                    "/api/v1/projects", headers=project_only, json={"code": "P-2", "name": "Two"}
                )
            ).status_code == 201

            task_only = await _login(
                client, email="work-task-only@andara.local", permissions=["task.create"]
            )
            assert (
                await client.post(
                    "/api/v1/projects", headers=task_only, json={"code": "P-3", "name": "No"}
                )
            ).status_code == 403
            assert (await client.get("/api/v1/tasks", headers=task_only)).status_code == 403
            assert (
                await client.post("/api/v1/tasks", headers=task_only, json={"title": "Own"})
            ).status_code == 201

            events = app.state.identity_audit.list_events(tenant_id="tenant_default")
            assert any(
                item.event_type == "project.created" and item.entity_id == project_id
                for item in events
            )
            assert any(
                item.event_type == "task.created" and item.entity_id == task_id for item in events
            )
    finally:
        if app is not None:
            await app.state.database.dispose()
        await _drop_database(database_name)


@pytest.mark.asyncio
async def test_approvals_use_scoped_subjects_and_separate_decision_authority() -> None:
    database_name = f"alos_approval_{uuid.uuid4().hex[:12]}"
    await _create_database(database_name)
    database_url = _database_url(database_name)
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
                GENESIS_BASE_URL="http://genesis.test",
                GENESIS_INTERNAL_TOKEN="approval-test-token",  # noqa: S106
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in ("/api/v1/approvals", "/api/v1/approvals/missing"):
                assert (await client.get(path)).status_code == 401
            assert (await client.post("/api/v1/approvals", json={})).status_code == 401

            denied = await _login(client, email="approval-denied@andara.local", permissions=[])
            legacy = await _login(
                client, email="approval-requester@andara.local",
                permissions=[
                    "work.read", "work.write", "approval.approve", "approval.return",
                    "approval.reject", "approval.hold",
                ],
            )
            decision_permissions = [
                "approval.read", "approval.approve", "approval.return",
                "approval.reject", "approval.hold",
            ]
            decider = await _login(
                client, email="approval-decider@andara.local", permissions=decision_permissions
            )
            approver_only = await _login(
                client, email="approval-approve-only@andara.local",
                permissions=["approval.read", "approval.approve"],
            )
            legacy_decider = await _login(
                client, email="approval-legacy-decider@andara.local",
                permissions=["work.read", "work.write"],
            )
            other_workspace = await _login(
                client,
                email="approval-other@andara.local",
                permissions=["work.read", "work.write"],
                workspace_id="workspace_hr", workspace_key="hr",
            )
            for path in ("/api/v1/approvals", "/api/v1/approvals/missing"):
                assert (await client.get(path, headers=denied)).status_code == 403
            assert (
                await client.post("/api/v1/approvals", headers=denied, json={})
            ).status_code == 403

            project = await client.post(
                "/api/v1/projects",
                headers=legacy,
                json={"code": "AP-1", "name": "Approval Project"},
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["project_id"]
            task = await client.post(
                "/api/v1/tasks", headers=legacy,
                json={"title": "Approval Task", "project_id": project_id},
            )
            assert task.status_code == 201, task.text
            task_id = task.json()["task_id"]
            remote_project = await client.post(
                "/api/v1/projects", headers=other_workspace,
                json={"code": "AP-HR", "name": "Remote"},
            )
            assert remote_project.status_code == 201, remote_project.text
            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                for suffix, tenant, organization in (
                    ("tenant", "another_tenant", "org_default"),
                    ("organization", "tenant_default", "another_organization"),
                ):
                    foreign_id = f"approval_foreign_{suffix}"
                    await postgres.execute(
                        "INSERT INTO core.projects "
                        "(project_id, tenant_id, organization_id, code, name, "
                        "created_at, updated_at) "
                        "VALUES ($1, $2, $3, $4, $5, now(), now())",
                        foreign_id, tenant, organization, f"FOREIGN-{suffix}", "Foreign project",
                    )
                    await postgres.execute(
                        "INSERT INTO core.project_workspaces (project_id, workspace_id) "
                        "VALUES ($1, $2)",
                        foreign_id, "workspace_property",
                    )
            finally:
                await postgres.close()
            for payload, expected in (
                ({"subject_type": "BUDGET", "subject_id": project_id}, 422),
                ({"subject_type": "PROJECT", "subject_id": "missing"}, 404),
                (
                    {"subject_type": "PROJECT", "subject_id": remote_project.json()["project_id"]},
                    404,
                ),
                ({"subject_type": "PROJECT", "subject_id": "approval_foreign_tenant"}, 404),
                ({"subject_type": "PROJECT", "subject_id": "approval_foreign_organization"}, 404),
                ({"subject_type": "PROJECT", "subject_id": project_id, "status": "APPROVED"}, 422),
                ({"subject_type": "TASK", "subject_id": task_id, "requested_by": "other"}, 422),
            ):
                result = await client.post("/api/v1/approvals", headers=legacy, json=payload)
                assert result.status_code == expected, result.text

            approvals: list[str] = []
            for subject_type, subject_id in (("PROJECT", project_id), ("TASK", task_id)):
                created = await client.post(
                    "/api/v1/approvals", headers=legacy,
                    json={
                        "subject_type": subject_type,
                        "subject_id": subject_id,
                        "reason": "Request reason",
                    },
                )
                assert created.status_code == 201, created.text
                data = created.json()
                approvals.append(data["approval_id"])
                assert data["status"] == "PENDING"
                assert data["requested_by"] == task.json()["created_by"]
                assert data["workspace_ids"] == ["workspace_property"]
                assert data["tenant_id"] == "tenant_default"
                assert data["organization_id"] == "org_default"
                assert data["decision"] is None and data["approver_actor_id"] is None
                assert data["decided_at"] is None and data["decision_reason"] is None
            assert len((await client.get("/api/v1/approvals", headers=legacy)).json()) == 2
            assert (
                await client.get(f"/api/v1/approvals/{approvals[0]}", headers=other_workspace)
            ).status_code == 404
            assert (
                await client.get("/api/v1/approvals?subject_type=BUDGET", headers=legacy)
            ).status_code == 422

            for action in ("approve", "return", "reject", "hold"):
                assert (
                    await client.post(
                        f"/api/v1/approvals/{approvals[0]}/{action}",
                        headers=legacy, json={"decision_reason": "Self"},
                    )
                ).status_code == 403
                assert (
                    await client.post(
                        f"/api/v1/approvals/{approvals[0]}/{action}",
                        headers=legacy_decider, json={"decision_reason": "No decision grant"},
                    )
                ).status_code == 403
            for action in ("return", "reject", "hold"):
                assert (
                    await client.post(
                        f"/api/v1/approvals/{approvals[0]}/{action}",
                        headers=approver_only, json={"decision_reason": "Reason"},
                    )
                ).status_code == 403
            approved = await client.post(
                f"/api/v1/approvals/{approvals[0]}/approve", headers=approver_only, json={}
            )
            assert approved.status_code == 200, approved.text
            assert approved.json()["status"] == approved.json()["decision"] == "APPROVED"
            assert (
                await client.post(
                    f"/api/v1/approvals/{approvals[1]}/return", headers=decider, json={}
                )
            ).status_code == 422
            returned = await client.post(
                f"/api/v1/approvals/{approvals[1]}/return", headers=decider,
                json={"decision_reason": "Needs changes"},
            )
            assert returned.status_code == 200, returned.text
            assert returned.json()["status"] == "RETURNED"
            assert returned.json()["decision"] == "RETURNED"
            assert returned.json()["reason"] == "Request reason"
            assert returned.json()["decision_reason"] == "Needs changes"
            assert returned.json()["decided_at"] is not None
            assert returned.json()["approver_actor_id"] != returned.json()["requested_by"]
            repeated = await client.post(
                f"/api/v1/approvals/{approvals[1]}/return", headers=decider,
                json={"decision_reason": "Needs changes"},
            )
            assert repeated.status_code == 200 and repeated.json() == returned.json()
            assert (
                await client.post(
                    f"/api/v1/approvals/{approvals[1]}/reject", headers=decider,
                    json={"decision_reason": "Different decision"},
                )
            ).status_code == 409
            assert (
                await client.post(
                    f"/api/v1/approvals/{approvals[1]}/return", headers=decider,
                    json={"decision_reason": "Changed reason"},
                )
            ).status_code == 409
            assert (
                await client.patch(
                    f"/api/v1/domains/shared/work_approvals/{approvals[1]}",
                    headers=legacy, json={"decision": "APPROVED"},
                )
            ).status_code == 409
            for action, expected_status, expected_decision in (
                ("reject", "REJECTED", "REJECTED"),
                ("hold", "HELD", "HOLD"),
            ):
                created = await client.post(
                    "/api/v1/approvals", headers=legacy,
                    json={"subject_type": "PROJECT", "subject_id": project_id},
                )
                assert created.status_code == 201, created.text
                next_id = created.json()["approval_id"]
                assert (
                    await client.post(
                        f"/api/v1/approvals/{next_id}/{action}",
                        headers=decider, json={"decision_reason": "   "},
                    )
                ).status_code == 422
                outcome = await client.post(
                    f"/api/v1/approvals/{next_id}/{action}",
                    headers=decider, json={"decision_reason": "Reviewed"},
                )
                assert outcome.status_code == 200, outcome.text
                assert outcome.json()["status"] == expected_status
                assert outcome.json()["decision"] == expected_decision
            events = app.state.identity_audit.list_events(tenant_id="tenant_default")
            assert sum(event.event_type == "approval.requested" for event in events) == 4
            assert sum(event.event_type == "approval.approve" for event in events) == 1
            assert sum(event.event_type == "approval.return" for event in events) == 1
            assert sum(event.event_type == "approval.reject" for event in events) == 1
            assert sum(event.event_type == "approval.hold" for event in events) == 1
    finally:
        if app is not None:
            await app.state.database.dispose()
        await _drop_database(database_name)


@pytest.mark.asyncio
async def test_project_and_task_lifecycle_requires_scoped_authority() -> None:
    database_name = f"alos_work_lifecycle_{uuid.uuid4().hex[:10]}"
    await _create_database(database_name)
    database_url = _database_url(database_name)
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
                GENESIS_BASE_URL="http://genesis.test",
                GENESIS_INTERNAL_TOKEN="work-lifecycle-test-token",  # noqa: S106
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            legacy = await _login(
                client,
                email="lifecycle-legacy@andara.local",
                permissions=["work.read", "work.write"],
            )
            authorized = await _login(
                client,
                email="lifecycle-authorized@andara.local",
                permissions=[
                    "project.read",
                    "project.update",
                    "project.archive",
                    "task.read",
                    "task.update",
                    "task.assign",
                    "task.complete",
                ],
            )
            target = await _login(
                client, email="lifecycle-target@andara.local", permissions=["work.read"]
            )
            target_actor = "actor_target"
            other_actor = "actor_other"
            unauthorized_actor = "actor_without_work_access"
            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                for actor_id, workspace_id, permission_refs in (
                    (target_actor, "workspace_property", '["work.read"]'),
                    (other_actor, "workspace_hr", '["work.read"]'),
                    (unauthorized_actor, "workspace_property", "[]"),
                ):
                    await postgres.execute(
                        "INSERT INTO core.actors "
                        "(actor_id,tenant_id,organization_id,display_name,active) "
                        "VALUES ($1,'tenant_default','org_default',$1,true)",
                        actor_id,
                    )
                    await postgres.execute(
                        "INSERT INTO core.workspace_memberships "
                        "(actor_id,workspace_id,tenant_id,organization_id,roles,permission_refs,"
                        "scope_refs,data_scope,active,created_at,effective_at,updated_at) "
                        "VALUES ($1,$2,'tenant_default','org_default','[\"DIVISION_MEMBER\"]',"
                        "$3::json,'[]','WORKSPACE',true,now(),now(),now())",
                        actor_id,
                        workspace_id,
                        permission_refs,
                    )
            finally:
                await postgres.close()

            project = await client.post(
                "/api/v1/projects",
                headers=legacy,
                json={"code": "LIFE-1", "name": "Lifecycle project"},
            )
            assert project.status_code == 201, project.text
            project_id = project.json()["project_id"]
            task = await client.post(
                "/api/v1/tasks",
                headers=legacy,
                json={"title": "Lifecycle task", "project_id": project_id},
            )
            assert task.status_code == 201, task.text
            task_id = task.json()["task_id"]

            for path in (
                "/api/v1/projects?status=UNKNOWN",
                "/api/v1/tasks?status=UNKNOWN",
                "/api/v1/tasks?priority=UNKNOWN",
            ):
                response = await client.get(path, headers=legacy)
                assert response.status_code == 422
                assert response.json()["code"] == "WORK_FILTER_INVALID"

            project_update = await client.patch(
                f"/api/v1/projects/{project_id}",
                headers=authorized,
                json={"name": "Updated project", "start_date": "2026-10-01"},
            )
            assert project_update.status_code == 200, project_update.text
            assert project_update.json()["name"] == "Updated project"
            assert project_update.json()["start_date"] == "2026-10-01"
            task_update = await client.patch(
                f"/api/v1/tasks/{task_id}",
                headers=authorized,
                json={
                    "title": "Updated task",
                    "priority": "HIGH",
                    "due_at": "2026-10-10T12:00:00Z",
                },
            )
            assert task_update.status_code == 200, task_update.text
            assert task_update.json()["priority"] == "HIGH"
            assert (
                await client.patch(
                    f"/api/v1/projects/{project_id}",
                    headers=legacy,
                    json={"description": "Legacy mutable edit"},
                )
            ).status_code == 200
            assert (
                await client.patch(
                    f"/api/v1/tasks/{task_id}",
                    headers=legacy,
                    json={"description": "Legacy mutable edit"},
                )
            ).status_code == 200

            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                await postgres.execute(
                    "INSERT INTO core.projects "
                    "(project_id,tenant_id,organization_id,code,name,status,created_at,updated_at) "
                    "VALUES ('project_hr','tenant_default','org_default','HR-1','HR project',"
                    "'PLANNED',now(),now())"
                )
                await postgres.execute(
                    "INSERT INTO core.project_workspaces (project_id,workspace_id) "
                    "VALUES ('project_hr','workspace_hr')"
                )
            finally:
                await postgres.close()
            assert (
                await client.patch(
                    f"/api/v1/tasks/{task_id}",
                    headers=authorized,
                    json={"project_id": "project_hr"},
                )
            ).status_code == 404
            assert (
                await client.patch(
                    "/api/v1/projects/project_hr",
                    headers=authorized,
                    json={"name": "Outside workspace"},
                )
            ).status_code == 404

            for path, payload in (
                (f"/api/v1/projects/{project_id}", {"status": "ARCHIVED"}),
                (f"/api/v1/projects/{project_id}", {"tenant_id": "forged"}),
                (f"/api/v1/tasks/{task_id}", {"status": "COMPLETED"}),
                (f"/api/v1/tasks/{task_id}", {"owner_actor_id": target_actor}),
                (f"/api/v1/tasks/{task_id}", {"workspace_ids": ["workspace_hr"]}),
            ):
                assert (
                    await client.patch(path, headers=authorized, json=payload)
                ).status_code == 422

            for path in (
                f"/api/v1/projects/{project_id}/archive",
                f"/api/v1/tasks/{task_id}/complete",
            ):
                assert (await client.post(path, headers=legacy)).status_code == 403
            assert (
                await client.post(
                    f"/api/v1/tasks/{task_id}/assign",
                    headers=legacy,
                    json={"owner_actor_id": target_actor},
                )
            ).status_code == 403
            for path, method, payload in (
                (f"/api/v1/projects/{project_id}", "PATCH", {"name": "No"}),
                (f"/api/v1/tasks/{task_id}", "PATCH", {"title": "No"}),
                (f"/api/v1/projects/{project_id}/archive", "POST", None),
                (f"/api/v1/tasks/{task_id}/complete", "POST", None),
            ):
                assert (
                    await client.request(method, path, headers=target, json=payload)
                ).status_code == 403

            other_assignment = await client.post(
                f"/api/v1/tasks/{task_id}/assign",
                headers=authorized,
                json={"owner_actor_id": other_actor},
            )
            assert other_assignment.status_code == 404, other_assignment.text
            assert (
                await client.post(
                    f"/api/v1/tasks/{task_id}/assign",
                    headers=authorized,
                    json={"owner_actor_id": unauthorized_actor},
                )
            ).status_code == 404
            assert (
                await client.post(
                    f"/api/v1/tasks/{task_id}/assign",
                    headers=authorized,
                    json={"owner_actor_id": "unknown_actor"},
                )
            ).status_code == 404
            assigned = await client.post(
                f"/api/v1/tasks/{task_id}/assign",
                headers=authorized,
                json={"owner_actor_id": target_actor},
            )
            assert assigned.status_code == 200, assigned.text
            assert assigned.json()["owner_actor_id"] == target_actor

            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                await postgres.execute(
                    "UPDATE core.workspace_memberships SET revoked_at=now(), active=false "
                    "WHERE actor_id=$1 AND workspace_id='workspace_property'",
                    target_actor,
                )
            finally:
                await postgres.close()
            assert (
                await client.post(
                    f"/api/v1/tasks/{task_id}/assign",
                    headers=authorized,
                    json={"owner_actor_id": target_actor},
                )
            ).status_code == 404

            completed = await client.post(f"/api/v1/tasks/{task_id}/complete", headers=authorized)
            assert completed.status_code == 200, completed.text
            assert completed.json()["status"] == "COMPLETED"
            repeated = await client.post(f"/api/v1/tasks/{task_id}/complete", headers=authorized)
            assert repeated.status_code == 200
            assert repeated.json()["updated_at"] == completed.json()["updated_at"]
            assert (
                await client.patch(
                    f"/api/v1/tasks/{task_id}",
                    headers=authorized,
                    json={"title": "Too late"},
                )
            ).status_code == 409
            archived = await client.post(
                f"/api/v1/projects/{project_id}/archive", headers=authorized
            )
            assert archived.status_code == 200, archived.text
            assert archived.json()["status"] == "ARCHIVED"
            repeated_archive = await client.post(
                f"/api/v1/projects/{project_id}/archive", headers=authorized
            )
            assert repeated_archive.status_code == 200
            assert repeated_archive.json()["updated_at"] == archived.json()["updated_at"]
            assert (
                await client.patch(
                    f"/api/v1/projects/{project_id}",
                    headers=authorized,
                    json={"name": "Too late"},
                )
            ).status_code == 409
            for collection, identifier in (("projects", project_id), ("tasks", task_id)):
                response = await client.patch(
                    f"/api/v1/domains/shared/{collection}/{identifier}",
                    headers=legacy,
                    json={"status": "COMPLETED"},
                )
                assert response.status_code == 409

            events = app.state.identity_audit.list_events(tenant_id="tenant_default")
            for event_type in (
                "project.updated",
                "project.archived",
                "task.updated",
                "task.assigned",
                "task.completed",
            ):
                assert any(event.event_type == event_type for event in events)
            assert sum(event.event_type == "project.archived" for event in events) == 1
            assert sum(event.event_type == "task.completed" for event in events) == 1
    finally:
        if app is not None:
            await app.state.database.dispose()
        await _drop_database(database_name)
