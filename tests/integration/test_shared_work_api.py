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
    client: httpx.AsyncClient, *, email: str, permissions: list[str]
) -> dict[str, str]:
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass!123",
            "display_name": "Shared Work Test",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_property",
            "workspace_key": "property",
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
            assert project_data["owner_actor_id"] is None

            task = await client.post(
                "/api/v1/tasks",
                headers=legacy,
                json={"title": "Task One", "project_id": project_id, "priority": "HIGH"},
            )
            assert task.status_code == 201, task.text
            task_data = task.json()
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
