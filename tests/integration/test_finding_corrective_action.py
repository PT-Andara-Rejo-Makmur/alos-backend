"""A finding cannot certify an unfinished linked corrective action as resolved."""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import update
from test_business_domains import Context
from test_business_domains import context as migrated_context
from test_strategy_planning_e2e import _register_and_login

pytestmark = pytest.mark.asyncio(loop_scope="module")
context = migrated_context


@pytest.mark.parametrize("task_state", ["none", "completed", "unfinished"])
async def test_corrective_action_completion_precedes_verification(
    context: Context, task_state: str
) -> None:
    async def actor(permissions: list[str]) -> dict[str, str]:
        headers, _ = await _register_and_login(
            context.client,
            email=f"corrective-{uuid4().hex}@alos.test",
            tenant_id="tenant_business",
            organization_id="org_business",
            workspace_id="workspace_business",
            roles=["DIVISION_MEMBER"],
            permissions=permissions,
        )
        return headers

    owner = await actor(
        [
            "task.read", "task.create", "task.complete", "finding.read", "finding.create",
            "finding.update", "finding.verify",
        ]
    )
    reviewer = await actor(["finding.read", "finding.verify", "finding.close"])
    data: dict[str, Any] = {"title": "Synthetic corrective-action regression", "severity": "HIGH"}
    task_id: str | None = None
    if task_state != "none":
        task = await context.client.post(
            "/api/v1/tasks", headers=owner, json={"title": "Synthetic corrective action"}
        )
        assert task.status_code == 201, task.text
        task_id = task.json()["task_id"]
        data["corrective_action_task_id"] = task_id
        if task_state == "completed":
            completed = await context.client.post(
                f"/api/v1/tasks/{task_id}/complete", headers=owner
            )
            assert completed.status_code == 200, completed.text

    created = await context.client.post("/api/v1/work/findings", headers=owner, json=data)
    assert created.status_code == 201, created.text
    path = f"/api/v1/work/findings/{created.json()['finding_id']}"
    for action in ("start", "submit-verification"):
        result = await context.client.post(f"{path}/{action}", headers=owner)
        assert result.status_code == 200, result.text
    assert (await context.client.post(f"{path}/verify", headers=owner)).status_code == 403

    if task_state == "unfinished":
        before = await context.audit_count("sales")
        denied = await context.client.post(f"{path}/verify", headers=reviewer)
        assert denied.status_code == 409, denied.text
        assert denied.json()["code"] == "FINDING_CORRECTIVE_ACTION_INCOMPLETE"
        current = await context.client.get(path, headers=owner)
        assert current.json()["status"] == "PENDING_VERIFICATION"
        assert current.json()["verifier_actor_id"] is None
        assert await context.audit_count("sales") == before
        completed = await context.client.post(f"/api/v1/tasks/{task_id}/complete", headers=owner)
        assert completed.status_code == 200, completed.text

    verified = await context.client.post(f"{path}/verify", headers=reviewer)
    assert verified.status_code == 200, verified.text
    assert verified.json()["status"] == "VERIFIED"
    closed = await context.client.post(f"{path}/close", headers=reviewer)
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"


async def test_legacy_verified_finding_cannot_close_with_unfinished_task(
    context: Context,
) -> None:
    accounts: list[dict[str, str]] = []
    for permissions in (
        ["task.read", "task.create", "task.complete", "finding.read", "finding.create"],
        ["finding.read", "finding.close"],
    ):
        headers, _ = await _register_and_login(
            context.client,
            email=f"legacy-corrective-{uuid4().hex}@alos.test",
            tenant_id="tenant_business",
            organization_id="org_business",
            workspace_id="workspace_business",
            roles=["DIVISION_MEMBER"],
            permissions=permissions,
        )
        accounts.append(headers)
    owner, closer = accounts
    task = await context.client.post(
        "/api/v1/tasks", headers=owner, json={"title": "Historical unfinished corrective fixture"}
    )
    assert task.status_code == 201, task.text
    task_id = task.json()["task_id"]
    created = await context.client.post(
        "/api/v1/work/findings",
        headers=owner,
        json={"title": "Historical inconsistent fixture", "severity": "HIGH",
              "corrective_action_task_id": task_id},
    )
    assert created.status_code == 201, created.text
    finding_id = created.json()["finding_id"]
    service = context.app.state.shared_work_service
    # Reproduce a historical inconsistent row produced by the previous bug, solely
    # inside this disposable test DB. This is a rejection fixture, not evidence of approval.
    async with service._session_factory() as session, session.begin():
        findings = await service._table(session, "work_findings")
        seeded = await session.execute(
            update(findings).where(
                findings.c.finding_id == finding_id,
                findings.c.tenant_id == "tenant_business",
                findings.c.organization_id == "org_business",
            ).values(status="VERIFIED")
        )
        assert seeded.rowcount == 1
    path = f"/api/v1/work/findings/{finding_id}"
    before = await context.audit_count("sales")
    denied = await context.client.post(f"{path}/close", headers=closer)
    assert denied.status_code == 409, denied.text
    assert denied.json()["code"] == "FINDING_CORRECTIVE_ACTION_INCOMPLETE"
    assert (await context.client.get(path, headers=owner)).json()["status"] == "VERIFIED"
    assert await context.audit_count("sales") == before
    completed = await context.client.post(f"/api/v1/tasks/{task_id}/complete", headers=owner)
    assert completed.status_code == 200, completed.text
    closed = await context.client.post(f"{path}/close", headers=closer)
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "CLOSED"
