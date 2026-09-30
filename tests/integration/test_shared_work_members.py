"""Read-only workspace member directory uses persisted Identity authority."""

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


async def _register(
    client: httpx.AsyncClient, email: str, permissions: list[str],
    *, workspace_id: str = "workspace_members", tenant_id: str = "tenant_members",
    organization_id: str = "org_members",
) -> tuple[dict[str, str], str]:
    profile = {
        "email": email, "password": "StrongPass!123", "display_name": email.split("@")[0],
        "tenant_id": tenant_id, "organization_id": organization_id,
        "workspace_id": workspace_id, "workspace_key": workspace_id,
        "workspace_name": workspace_id, "workspace_type": "BUSINESS",
        "division_code": "UNASSIGNED", "role_refs": ["DIVISION_MEMBER"],
        "permission_refs": permissions, "scope_refs": [], "data_scope": "WORKSPACE",
    }
    registered = await client.post("/api/v1/auth/register", json=profile)
    assert registered.status_code == 201, registered.text
    logged_in = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "StrongPass!123"}
    )
    assert logged_in.status_code == 200, logged_in.text
    headers = {"Authorization": f"Bearer {logged_in.json()['access_token']}"}
    return headers, registered.json()["actor_id"]


@pytest.mark.asyncio
async def test_workspace_member_directory_is_scoped_and_filters_ineligible_accounts() -> None:
    name = f"alos_members_{uuid.uuid4().hex[:10]}"
    admin = await asyncpg.connect(_database_url("postgres").replace("+asyncpg", ""))
    await admin.execute(f'CREATE DATABASE "{name}"')
    await admin.close()
    app = None
    try:
        await asyncio.to_thread(
            subprocess.run, [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT, env={**os.environ, "DATABASE_URL": _database_url(name)},
            check=True, capture_output=True, text=True,
        )
        app = create_app(Settings(
            _env_file=None, APP_ENV="development", DATABASE_URL=_database_url(name),
            ALOS_CONTRACTS_PATH=CONTRACTS_ROOT, ENABLE_TEST_REGISTRATION=True,
        ))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            path = "/api/v1/workspace-members"
            assert (await client.get(path)).status_code == 401
            denied, _ = await _register(client, "denied@alos.test", [])
            assert (await client.get(path, headers=denied)).status_code == 403
            reader, reader_id = await _register(
                client, "reader@alos.test", ["task.assign", "finding.assign"]
            )
            eligible, eligible_id = await _register(
                client, "eligible@alos.test", ["task.read", "finding.read"]
            )
            _, inactive_id = await _register(
                client, "inactive@alos.test", ["task.read"]
            )
            _, revoked_id = await _register(
                client, "revoked@alos.test", ["task.read"]
            )
            _, expired_id = await _register(
                client, "expired@alos.test", ["task.read"]
            )
            remote_headers, _ = await _register(
                client, "remote@alos.test", ["task.read", "document.read",
                                             "work.relation.link"],
                workspace_id="workspace_remote"
            )
            await _register(
                client, "foreign-org@alos.test", ["task.read"],
                workspace_id="workspace_foreign_org", organization_id="org_foreign",
            )
            await _register(
                client, "foreign-tenant@alos.test", ["task.read"],
                workspace_id="workspace_foreign_tenant", tenant_id="tenant_foreign",
                organization_id="org_tenant_foreign",
            )
            postgres = await asyncpg.connect(_database_url(name).replace("+asyncpg", ""))
            try:
                await postgres.execute(
                    "UPDATE core.auth_accounts SET active=false WHERE actor_id=$1", inactive_id
                )
                await postgres.execute(
                    "UPDATE core.workspace_memberships SET revoked_at=now() "
                    "WHERE actor_id=$1", revoked_id,
                )
                await postgres.execute(
                    "UPDATE core.workspace_memberships SET expires_at=now()-interval '1 day' "
                    "WHERE actor_id=$1", expired_id,
                )
            finally:
                await postgres.close()
            response = await client.get(path, headers=reader)
            assert response.status_code == 200, response.text
            members = {item["actor_id"]: item for item in response.json()}
            assert reader_id in members and eligible_id in members
            assert inactive_id not in members and revoked_id not in members
            assert expired_id not in members
            assert members[eligible_id]["task_assignable"] is True
            assert members[eligible_id]["finding_assignable"] is True
            assert members[eligible_id]["workspace_id"] == "workspace_members"
            assert members[eligible_id]["display_name"] == "eligible"
            assert members[reader_id]["task_assignable"] is False
            assert (await client.get(path, headers=eligible)).status_code == 200

            definition_owner, owner_id = await _register(
                client, "definition-owner@alos.test", ["report.read", "report.create"]
            )
            report_reader, _ = await _register(
                client, "definition-reader@alos.test", ["report.read"]
            )
            definitions_path = "/api/v1/work/reports/definitions"
            assert (await client.get(definitions_path)).status_code == 401
            assert (
                await client.post(definitions_path, headers=denied, json={})
            ).status_code == 403
            created = await client.post(
                definitions_path,
                headers=definition_owner,
                json={
                    "name": "Monthly Review", "report_type": "AUDIT",
                    "frequency": "MONTHLY", "review_required": True,
                    "recipients": ["ops@example.test"], "sections": ["summary"],
                    "data_sources": ["core.tasks"],
                },
            )
            assert created.status_code == 201, created.text
            definition = created.json()
            assert definition["owner_actor_id"] == owner_id
            assert definition["workspace_name"] == "workspace_members"
            definition_path = f"{definitions_path}/{definition['report_definition_id']}"
            assert (await client.get(definition_path, headers=report_reader)).status_code == 200
            assert (
                await client.patch(
                    definition_path, headers=report_reader, json={"name": "Changed"}
                )
            ).status_code == 403
            changed = await client.patch(
                definition_path, headers=definition_owner,
                json={"frequency": "QUARTERLY"},
            )
            assert changed.status_code == 200, changed.text
            assert changed.json()["frequency"] == "QUARTERLY"
            assert len((await client.get(definitions_path, headers=report_reader)).json()) == 1

            work_owner, work_owner_id = await _register(
                client, "work-owner@alos.test",
                ["project.create", "project.read", "task.create", "task.read",
                 "document.create", "document.read", "document.version", "document.review",
                 "report.create", "report.read",
                 "finding.create", "finding.read", "finding.update",
                 "approval.request", "approval.read",
                 "work.evidence.link", "work.comment.create",
                 "work.relation.link", "work.checklist.manage"],
            )
            project_created = await client.post(
                "/api/v1/projects", headers=work_owner,
                json={"code": "PROOF", "name": "Proof Project"},
            )
            assert project_created.status_code == 201, project_created.text
            project_id = project_created.json()["project_id"]
            task_created = await client.post(
                "/api/v1/tasks", headers=work_owner,
                json={"title": "Related Task", "project_id": project_id,
                      "start_date": "2026-09-30"},
            )
            assert task_created.status_code == 201, task_created.text
            document_created = await client.post(
                "/api/v1/documents", headers=work_owner,
                json={"title": "Related Document", "category": "POLICY",
                      "data_classification": "INTERNAL", "project_id": project_id},
            )
            assert document_created.status_code == 201, document_created.text
            report_created = await client.post(
                "/api/v1/work/reports/results", headers=work_owner,
                json={"title": "Related Report", "report_type": "AUDIT",
                      "project_id": project_id},
            )
            assert report_created.status_code == 201, report_created.text
            finding_created = await client.post(
                "/api/v1/work/findings", headers=work_owner,
                json={"title": "Related Finding", "project_id": project_id,
                      "corrective_action_task_id": task_created.json()["task_id"]},
            )
            assert finding_created.status_code == 201, finding_created.text
            approval_created = await client.post(
                "/api/v1/approvals", headers=work_owner,
                json={"subject_type": "PROJECT", "subject_id": project_id},
            )
            assert approval_created.status_code == 201, approval_created.text
            document_id = document_created.json()["document_id"]
            task_id = task_created.json()["task_id"]
            approval_id = approval_created.json()["approval_id"]
            assert (await client.post(
                f"/api/v1/documents/{document_id}/links", headers=denied,
                json={"target_type": "TASK", "target_id": task_id},
            )).status_code == 403
            linked = await client.post(
                f"/api/v1/documents/{document_id}/links", headers=work_owner,
                json={"target_type": "TASK", "target_id": task_id},
            )
            assert linked.status_code == 201, linked.text
            assert linked.json()["title"] == "Related Task"
            repeated_link = await client.post(
                f"/api/v1/documents/{document_id}/links", headers=work_owner,
                json={"target_type": "TASK", "target_id": task_id},
            )
            assert repeated_link.status_code == 201
            assert (await client.post(
                f"/api/v1/documents/{document_id}/links", headers=work_owner,
                json={"target_type": "APPROVAL", "target_id": approval_id},
            )).status_code == 201
            assert (await client.post(
                f"/api/v1/documents/{document_id}/links", headers=work_owner,
                json={"target_type": "TASK", "target_id": task_id,
                      "tenant_id": "foreign"},
            )).status_code == 422
            assert (await client.get(
                f"/api/v1/work/DOCUMENT/{document_id}/relations", headers=work_owner,
            )).status_code == 200
            task_relations = await client.get(
                f"/api/v1/work/TASK/{task_id}/relations", headers=work_owner,
            )
            assert task_relations.status_code == 200
            assert any(item["entity_id"] == document_id for item in task_relations.json())
            assert (await client.get(
                f"/api/v1/work/DOCUMENT/{document_id}/relations", headers=remote_headers,
            )).status_code == 404
            assert (await client.post(
                f"/api/v1/documents/{document_id}/links", headers=remote_headers,
                json={"target_type": "TASK", "target_id": task_id},
            )).status_code == 404
            document_detail = await client.get(
                f"/api/v1/documents/{document_id}", headers=work_owner,
            )
            assert document_detail.json()["tasks_count"] == 1
            assert document_detail.json()["approvals_count"] == 1
            task_detail = await client.get(f"/api/v1/tasks/{task_id}", headers=work_owner)
            assert task_detail.json()["documents_count"] == 1
            checklist_path = f"/api/v1/work/TASK/{task_id}/checklist"
            assert (await client.post(checklist_path, headers=denied,
                                      json={"body": "Inspect"})).status_code == 403
            checklist = await client.post(
                checklist_path, headers=work_owner, json={"body": "Inspect"},
            )
            assert checklist.status_code == 201, checklist.text
            item_id = checklist.json()["item_id"]
            completed_path = f"{checklist_path}/{item_id}/complete"
            completed = await client.post(completed_path, headers=work_owner)
            assert completed.status_code == 200, completed.text
            assert completed.json()["completed"] is True
            repeated_completion = await client.post(completed_path, headers=work_owner)
            assert repeated_completion.json() == completed.json()
            assert len((await client.get(checklist_path, headers=work_owner)).json()) == 1
            task_activity = (await client.get(
                f"/api/v1/work/TASK/{task_id}/activity", headers=work_owner,
            )).json()
            assert sum(item["event_type"] == "task.checklist_item_created"
                       for item in task_activity) == 1
            assert sum(item["event_type"] == "task.checklist_item_completed"
                       for item in task_activity) == 1
            document_activity = (await client.get(
                f"/api/v1/work/DOCUMENT/{document_id}/activity", headers=work_owner,
            )).json()
            assert sum(item["event_type"] == "document.linked"
                       for item in document_activity) == 2
            related = await client.get(
                f"/api/v1/projects/{project_id}/relations", headers=work_owner
            )
            assert related.status_code == 200, related.text
            assert {item["entity_type"] for item in related.json()} == {
                "TASK", "DOCUMENT", "REPORT", "FINDING", "APPROVAL"
            }
            project_projection = (await client.get(
                f"/api/v1/projects/{project_id}", headers=work_owner
            )).json()
            assert project_projection["progress_percentage"] == 0
            assert project_projection["owner_actor_id"] == work_owner_id
            assert project_projection["owner_name"] == "work-owner"
            assert project_projection["risk_level"] == "LOW"
            assert project_projection["tasks_count"] == 1
            assert project_projection["documents_count"] == 1
            assert project_projection["reports_count"] == 1
            assert project_projection["findings_count"] == 1
            assert project_projection["approvals_count"] == 1
            task_projection = (await client.get(
                f"/api/v1/tasks/{task_created.json()['task_id']}", headers=work_owner
            )).json()
            assert task_projection["project_name"] == "Proof Project"
            assert task_projection["creator_name"] == "work-owner"
            assert task_projection["start_date"] == "2026-09-30"
            assert task_projection["workspace_name"] == "workspace_members"
            assert task_projection["findings_count"] == 1
            assert approval_created.json()["subject_title"] == "Proof Project"
            assert finding_created.json()["corrective_action_task_title"] == "Related Task"
            risk_change = await client.patch(
                f"/api/v1/work/findings/{finding_created.json()['finding_id']}",
                headers=work_owner, json={"severity": "CRITICAL"},
            )
            assert risk_change.status_code == 200, risk_change.text
            assert (await client.get(
                f"/api/v1/projects/{project_id}", headers=work_owner
            )).json()["risk_level"] == "CRITICAL"
            relation_path = f"/api/v1/work/PROJECT/{project_id}"
            postgres = await asyncpg.connect(_database_url(name).replace("+asyncpg", ""))
            try:
                await postgres.execute(
                    "INSERT INTO core.sources "
                    "(source_id,tenant_id,organization_id,workspace_id,title,source_type,"
                    "data_classification,created_by,created_at) VALUES "
                    "('proof-source','tenant_members','org_members','workspace_members',"
                    "'Verified source','PDF','INTERNAL','fixture',now()); "
                    "INSERT INTO core.source_versions "
                    "(source_id,source_version,tenant_id,organization_id,workspace_id,"
                    "storage_uri,content_hash,status,created_by,created_at) VALUES "
                    "('proof-source','1','tenant_members','org_members','workspace_members',"
                    "'urn:alos:source:proof','sha256:' || repeat('a',64),"
                    "'VERIFIED','fixture',now())"
                )
                await postgres.execute(
                    "INSERT INTO evidence.evidence_refs "
                    "(evidence_id, tenant_id, organization_id, workspace_id, source_id, "
                    "uri, content_hash, data_classification, validation_status, "
                    "metadata_payload, captured_at) VALUES "
                    "($1,$2,$3,$4,$5,$6,$7,$8,$9,'{}',now())",
                    "proof-evidence", "tenant_members", "org_members", "workspace_members",
                    "proof-source", "source://proof", "sha256:" + "a" * 64,
                    "INTERNAL", "VERIFIED",
                )
            finally:
                await postgres.close()
            document_id = document_created.json()["document_id"]
            options_path = f"/api/v1/documents/{document_id}/source-options"
            options = await client.get(options_path, headers=work_owner)
            assert options.status_code == 200, options.text
            assert options.json() == [{
                "source_id": "proof-source", "source_title": "Verified source",
                "source_version": "1", "content_hash": "sha256:" + "a" * 64,
            }]
            assert "storage_uri" not in options.text
            created_version = await client.post(
                f"/api/v1/documents/{document_id}/versions", headers=work_owner,
                json={"version": "1.0", "source_id": "proof-source", "source_version": "1"},
            )
            assert created_version.status_code == 201, created_version.text
            assert created_version.json()["content_hash"] == "sha256:" + "a" * 64
            assert created_version.json()["source_title"] == "Verified source"
            assert created_version.json()["creator_name"] == "work-owner"
            history = await client.get(
                f"/api/v1/documents/{document_id}/versions", headers=work_owner
            )
            assert history.status_code == 200, history.text
            assert history.json()[0]["source_title"] == "Verified source"
            assert history.json()[0]["creator_name"] == "work-owner"
            reviewed = await client.post(
                f"/api/v1/documents/{document_id}/review", headers=work_owner
            )
            assert reviewed.status_code == 200, reviewed.text
            assert (await client.get(options_path, headers=work_owner)).json() == []
            linked = await client.post(
                relation_path + "/evidence", headers=work_owner,
                json={"evidence_id": "proof-evidence"},
            )
            assert linked.status_code == 201, linked.text
            assert linked.json()["source_id"] == "proof-source"
            assert (await client.get(
                f"/api/v1/projects/{project_id}", headers=work_owner
            )).json()["evidence_count"] == 1
            assert len((await client.get(
                relation_path + "/evidence", headers=work_owner
            )).json()) == 1
            commented = await client.post(
                relation_path + "/comments", headers=work_owner,
                json={"body": "Verified source attached"},
            )
            assert commented.status_code == 201, commented.text
            assert commented.json()["actor_id"] == work_owner_id
            assert len((await client.get(
                relation_path + "/comments", headers=work_owner
            )).json()) == 1
            activity = await client.get(relation_path + "/activity", headers=work_owner)
            assert activity.status_code == 200, activity.text
            assert {item["event_type"] for item in activity.json()} >= {
                "project.created", "project.evidence_linked", "project.commented"
            }
            assert (await client.get(
                relation_path + "/evidence", headers=denied
            )).status_code == 403
            remote_reader, _ = await _register(
                client, "remote-reader@alos.test", ["project.read"],
                workspace_id="workspace_remote",
            )
            assert (await client.get(
                relation_path + "/evidence", headers=remote_reader
            )).status_code == 404
    finally:
        if app is not None:
            await app.state.database.dispose()
        admin = await asyncpg.connect(_database_url("postgres").replace("+asyncpg", ""))
        try:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await admin.close()
