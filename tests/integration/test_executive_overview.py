"""Executive closure against real persisted Strategy and Shared Work authority."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import insert, update
from sqlalchemy.exc import OperationalError
from test_strategy_planning_e2e import (
    CONTRACTS_ROOT,
    _asyncpg_url,
    _database_url,
    _plan_payload,
    _register_and_login,
)

from alos.config import Settings
from alos.identity import Principal
from alos.main import create_app
from alos.security.errors import PlatformError

pytestmark = pytest.mark.asyncio(loop_scope="module")
STAMP = datetime(2027, 2, 3, 4, 5, tzinfo=UTC)


@dataclass
class Context:
    app: FastAPI
    client: httpx.AsyncClient
    headers: dict[str, dict[str, str]]
    principals: dict[str, Principal]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def context() -> AsyncIterator[Context]:
    name = "alos_executive_projection_test"
    admin = await asyncpg.connect(_asyncpg_url(_database_url("postgres")))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    url = _database_url(name)
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
    assert process.returncode == 0, (stdout + stderr).decode()
    app = create_app(
        Settings(
            _env_file=None,
            APP_ENV="development",
            DATABASE_URL=url,
            ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
            ENABLE_TEST_REGISTRATION=True,
        )
    )
    headers: dict[str, dict[str, str]] = {}
    principals: dict[str, Principal] = {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for label, tenant, org, workspace, role in (
            ("company", "tenant_exec", "org_exec", "workspace_company", "EXECUTIVE"),
            ("other_workspace", "tenant_exec", "org_exec", "workspace_else", "EXECUTIVE"),
            ("other_org", "tenant_exec", "org_else", "workspace_org_else", "EXECUTIVE"),
            (
                "other_tenant",
                "tenant_else",
                "org_tenant_else",
                "workspace_tenant_else",
                "EXECUTIVE",
            ),
            ("empty", "tenant_exec", "org_empty", "workspace_empty", "EXECUTIVE"),
            ("it", "tenant_exec", "org_exec", "workspace_company", "IT_ADMIN"),
            ("member", "tenant_exec", "org_exec", "workspace_company", "DIVISION_MEMBER"),
        ):
            headers[label], actor = await _register_and_login(
                client,
                email=f"{label}@executive.test",
                tenant_id=tenant,
                organization_id=org,
                workspace_id=workspace,
                roles=[role],
                permissions=["strategy.read", "work.read"],
            )
            principals[label] = Principal(
                actor,
                tenant,
                org,
                workspace,
                permissions=frozenset({"strategy.read", "work.read"}),
                roles=frozenset({role}),
            )
        service = app.state.shared_work_service
        for label in ("company", "other_workspace", "other_org", "other_tenant"):
            principal = principals[label]
            project = await service.create_project(principal, {"code": label, "name": label})
            task = await service.create_task(
                principal,
                {
                    "title": f"task {label}",
                    "project_id": project["project_id"],
                },
            )
            await service.request_approval(
                principal,
                {
                    "subject_type": "TASK",
                    "subject_id": task["task_id"],
                },
            )
            await service.create_report(principal, {"title": label, "report_type": "OPERATIONS"})
            await service.create_document(
                principal,
                {
                    "title": label,
                    "category": "GENERAL",
                    "data_classification": "INTERNAL",
                },
            )
            await service.create_finding(principal, {"title": label, "severity": "LOW"})
        principal = principals["company"]
        for status in ("ACTIVE", "ON_HOLD", "COMPLETED"):
            row = await service.create_project(principal, {"code": status, "name": status})
            async with app.state.database.session_factory.begin() as session:
                table = await service._table(session, "projects")
                await session.execute(
                    update(table)
                    .where(table.c.project_id == row["project_id"])
                    .values(status=status)
                )
        for status, priority, due in (
            ("OPEN", "NORMAL", None),
            ("BLOCKED", "CRITICAL", "2020-01-01T00:00:00Z"),
            ("UNDER_REVIEW", "NORMAL", None),
            ("COMPLETED", "CRITICAL", "2020-01-01T00:00:00Z"),
            ("CANCELLED", "CRITICAL", "2020-01-01T00:00:00Z"),
        ):
            row = await service.create_task(
                principal,
                {
                    "title": status,
                    "priority": priority,
                    "due_at": due,
                },
            )
            async with app.state.database.session_factory.begin() as session:
                table = await service._table(session, "tasks")
                await session.execute(
                    update(table).where(table.c.task_id == row["task_id"]).values(status=status)
                )
        tasks = await service.list_tasks(principal, status=None, priority=None, search=None)
        for status in ("APPROVED", "RETURNED", "REJECTED", "HELD"):
            row = await service.request_approval(
                principal,
                {
                    "subject_type": "TASK",
                    "subject_id": tasks[0]["task_id"],
                },
            )
            async with app.state.database.session_factory.begin() as session:
                table = await service._table(session, "work_approvals")
                await session.execute(
                    update(table)
                    .where(table.c.approval_id == row["approval_id"])
                    .values(
                        status=status,
                        decision="HOLD" if status == "HELD" else status,
                        decided_at=STAMP,
                    )
                )
        for status, severity in (
            ("OPEN", "CRITICAL"),
            ("ASSIGNED", "HIGH"),
            ("IN_PROGRESS", "MEDIUM"),
            ("PENDING_VERIFICATION", "HIGH"),
            ("VERIFIED", "CRITICAL"),
            ("CLOSED", "CRITICAL"),
        ):
            row = await service.create_finding(
                principal,
                {
                    "title": f"critical wording {status}",
                    "severity": severity,
                },
            )
            async with app.state.database.session_factory.begin() as session:
                table = await service._table(session, "work_findings")
                await session.execute(
                    update(table)
                    .where(table.c.finding_id == row["finding_id"])
                    .values(status=status)
                )
        async with app.state.database.session_factory.begin() as session:
            for table_name in ("projects", "tasks", "work_findings", "work_reports", "documents"):
                table = await service._table(session, table_name)
                await session.execute(update(table).values(updated_at=STAMP))
            table = await service._table(session, "work_approvals")
            await session.execute(update(table).values(requested_at=STAMP))
            # More than the ordinary list endpoint's 200 limit: counts must remain exact.
            projects = await service._table(session, "projects")
            links = await service._table(session, "project_workspaces")
            await session.execute(
                insert(projects),
                [
                    {
                        "project_id": f"project_bulk_{i}",
                        "tenant_id": principal.tenant_id,
                        "organization_id": principal.organization_id,
                        "code": f"BULK-{i}",
                        "name": f"Bulk {i}",
                        "status": "PLANNED",
                        "created_at": STAMP,
                        "updated_at": STAMP,
                    }
                    for i in range(201)
                ],
            )
            await session.execute(
                insert(links),
                [
                    {"project_id": f"project_bulk_{i}", "workspace_id": principal.workspace_id}
                    for i in range(201)
                ],
            )
        plan = {**_plan_payload("plan.executive"), "owner_workspace_id": principal.workspace_id}
        response = await client.post(
            "/api/v1/strategy/plans", headers=headers["company"], json=plan
        )
        assert response.status_code == 201, response.text
        yield Context(app, client, headers, principals)
    await app.state.database.dispose()


async def read(context: Context, label: str = "company") -> dict[str, Any]:
    response = await context.client.get(
        "/api/v1/executive/overview", headers=context.headers[label]
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_authoritative_sources_contract_lineage_and_exact_counts(context: Context):
    body = await read(context)
    assert body["strategy"]["status"] == "CONNECTED"
    assert body["shared_work"]["status"] == "CONNECTED"
    assert body["strategy"]["authoritative"] is body["shared_work"]["authoritative"] is True
    data = body["shared_work_data"]
    assert data["counts"]["projects"] == 205 and len(data["projects"]) == 50
    assert data["counts"]["active_projects"] == 1
    assert data["counts"]["on_hold_projects"] == 1
    assert data["counts"]["completed_projects"] == 1
    assert datetime.fromisoformat(body["shared_work"]["last_updated_at"]) == STAMP
    assert datetime.fromisoformat(body["last_updated_at"]) == STAMP
    context.app.state.factory_contracts.validate(
        "https://schemas.alos.dev/v1/executive/executive-overview-projection.schema.json", body
    )
    assert all(
        domain["status"]
        == (
            "CONNECTED_EMPTY"
            if domain["domain"] in {"SALES", "PROPERTY", "FINANCE"}
            else "UNAVAILABLE"
        )
        for domain in body["domains"]
    )


async def test_pending_approval_never_includes_terminal_returned_or_held(context: Context):
    data = (await read(context))["shared_work_data"]
    assert data["counts"]["approvals"] == 5 and data["counts"]["pending_approvals"] == 1
    assert {row["status"] for row in data["approvals"]} == {
        "PENDING",
        "APPROVED",
        "RETURNED",
        "REJECTED",
        "HELD",
    }
    assert next(row for row in data["approvals"] if row["status"] == "HELD")["decision"] == "HOLD"


async def test_findings_are_canonical_and_closed_or_verified_are_not_active(context: Context):
    data = (await read(context))["shared_work_data"]
    counts = data["counts"]
    assert counts["findings"] == 7 and counts["active_findings"] == 5
    assert counts["open_findings"] == 2 and counts["critical_findings"] == 1
    assert counts["high_findings"] == 2 and counts["pending_verification_findings"] == 1
    assert (
        next(row for row in data["findings"] if row["status"] == "IN_PROGRESS")["severity"]
        == "MEDIUM"
    )


async def test_tasks_use_actual_due_dates_and_terminal_states(context: Context):
    counts = (await read(context))["shared_work_data"]["counts"]
    assert counts["tasks"] == 6
    assert counts["overdue_tasks"] == counts["blocked_tasks"] == counts["critical_tasks"] == 1
    assert counts["pending_review_tasks"] == 1


@pytest.mark.parametrize("label", ["other_workspace", "other_org", "other_tenant"])
async def test_all_shared_work_collections_remain_scope_isolated(context: Context, label: str):
    body = await read(context, label)
    principal = context.principals[label]
    for resource in ("projects", "tasks", "approvals", "findings", "reports", "documents"):
        assert body["shared_work_data"]["counts"][resource] == 1
        for row in body["shared_work_data"][resource]:
            assert row["tenant_id"] == principal.tenant_id
            assert row["organization_id"] == principal.organization_id
            assert row.get("workspace_ids", [row.get("workspace_id")]) == [principal.workspace_id]
    if label == "other_workspace":
        assert body["strategy"]["status"] == "CONNECTED"  # existing company Strategy authority
    else:
        assert body["strategy"]["status"] == "CONNECTED_EMPTY"


async def test_connected_empty_is_authoritative_and_unknown_times_stay_null(context: Context):
    body = await read(context, "empty")
    for source in ("strategy", "shared_work"):
        assert body[source]["status"] == "CONNECTED_EMPTY"
        assert body[source]["authoritative"] is True
        assert body[source]["last_updated_at"] is None
    assert body["last_updated_at"] is None
    assert not any(body["shared_work_data"]["counts"].values())


@pytest.mark.parametrize("label", ["it", "member"])
async def test_non_executive_even_it_admin_is_denied(context: Context, label: str):
    response = await context.client.get(
        "/api/v1/executive/overview", headers=context.headers[label]
    )
    assert response.status_code == 403


async def test_missing_authentication_is_401(context: Context):
    assert (await context.client.get("/api/v1/executive/overview")).status_code == 401


@pytest.mark.parametrize("source", ["strategy", "shared_work"])
async def test_partial_retrieval_failure_retains_other_persisted_source(
    context: Context,
    monkeypatch,
    source: str,
):
    async def fail(*args, **kwargs):
        raise OperationalError("private database detail", {}, Exception("unreachable"))

    if source == "strategy":
        monkeypatch.setattr(context.app.state.strategy_repository, "list_plans", fail)
    else:
        monkeypatch.setattr(context.app.state.shared_work_service, "executive_summary", fail)
    body = await read(context)
    other = "shared_work" if source == "strategy" else "strategy"
    assert body[source]["status"] == "ERROR" and body[source + "_data"] is None
    assert body[source]["authoritative"] is True
    assert body[source]["last_updated_at"] is None
    assert body[other]["status"] == "CONNECTED" and body[other + "_data"] is not None
    assert "private database detail" not in str(body)


async def test_security_failure_is_never_swallowed_as_partial_data(context: Context, monkeypatch):
    async def deny(*args, **kwargs):
        raise PlatformError("WORKSPACE_ACCESS_DENIED", "Denied", status_code=403)

    monkeypatch.setattr(context.app.state.shared_work_service, "executive_summary", deny)
    response = await context.client.get(
        "/api/v1/executive/overview", headers=context.headers["company"]
    )
    assert response.status_code == 403


async def test_inactive_and_missing_work_permissions_fail_closed(context: Context):
    actor = context.principals["company"]
    for denied in (
        replace(actor, active=False),
        replace(actor, permissions=frozenset({"strategy.read"})),
    ):
        with pytest.raises(PlatformError) as failure:
            await context.app.state.executive_service.overview(denied)
        assert failure.value.status_code == 403


async def test_active_workspace_switch_uses_membership_visibility(context: Context):
    actor = context.principals["company"]
    await context.app.state.auth_service.assign_membership(
        actor.actor_id,
        {"workspace_id": "workspace_else", "role_refs": ["EXECUTIVE"]},
        tenant_id=actor.tenant_id,
        organization_id=actor.organization_id,
    )
    response = await context.client.put(
        "/api/v1/auth/active-workspace",
        headers=context.headers["company"],
        json={"workspace_id": "workspace_else"},
    )
    assert response.status_code == 200, response.text
    try:
        body = await read(context)
        assert body["workspace_id"] == "workspace_else"
        assert body["shared_work_data"]["counts"]["projects"] == 1
        assert body["strategy"]["status"] == "CONNECTED"
    finally:
        response = await context.client.put(
            "/api/v1/auth/active-workspace",
            headers=context.headers["company"],
            json={"workspace_id": actor.workspace_id},
        )
        assert response.status_code == 200


async def test_missing_contracts_fail_closed(context: Context, monkeypatch):
    monkeypatch.setattr(context.app.state.settings, "ALOS_CONTRACTS_PATH", None)
    response = await context.client.get(
        "/api/v1/executive/overview", headers=context.headers["company"]
    )
    assert response.status_code == 503
    assert "shared_work_data" not in response.json()
