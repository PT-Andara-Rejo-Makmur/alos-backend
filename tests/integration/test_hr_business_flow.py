"""Hiring uses the employee source; onboarding and offboarding validate actual access."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from test_business_domains import Context
from test_business_domains import context as migrated_context
from test_business_processes import action, configure, submit
from test_strategy_planning_e2e import _register_and_login

pytestmark = pytest.mark.asyncio(loop_scope="module")
context = migrated_context


async def test_hiring_onboarding_and_offboarding_reuse_identity(context: Context) -> None:
    ctx = context
    identity_headers, _ = await _register_and_login(
        ctx.client,
        email="hr-identity@business.test",
        tenant_id="tenant_business",
        organization_id="org_business",
        workspace_id="workspace_business",
        roles=["IT_ADMIN"],
        permissions=[
            "it.write",
            "identity.accounts.manage",
            "identity.memberships.manage",
            "identity.memberships.read",
        ],
    )
    ctx.headers["identity"] = identity_headers
    today = date.today().isoformat()
    recruitment = await ctx.create(
        "hr",
        "recruitments",
        {
            "position_title": "Staf operasional",
            "department_code": "OPS",
            "employment_type": "PERMANENT",
            "opened_at": datetime.now(UTC).isoformat(),
            "requesting_workspace_id": "workspace_else",
            "reason": "Kebutuhan tenaga kerja divisi",
            "headcount": 1,
        },
    )
    candidate = await ctx.create(
        "hr",
        "candidates",
        {
            "recruitment_id": recruitment["recruitment_id"],
            "full_name": "Karyawan Operasional",
            "email": "hired@business.test",
            "source": "Lamaran langsung",
        },
    )
    hire_body = {
        "employee_number": uuid4().hex,
        "join_date": today,
        "reason": "Hasil wawancara diterima",
    }
    path = f"/api/v1/hr/candidates/{candidate['candidate_id']}/hire"
    for user, expected in (("member", 403), ("lead", 409), ("org", 404), ("tenant", 404)):
        blocked = await ctx.client.post(path, headers=ctx.headers[user], json=hire_body)
        assert blocked.status_code == expected, blocked.text
    await ctx.transition("hr", "candidates", candidate["candidate_id"], "SCREENING")
    await ctx.transition("hr", "candidates", candidate["candidate_id"], "INTERVIEW")
    interview = await ctx.create(
        "hr",
        "interviews",
        {
            "candidate_id": candidate["candidate_id"],
            "scheduled_at": datetime.now(UTC).isoformat(),
            "notes": "Kemampuan dan kesiapan memenuhi kebutuhan divisi",
        },
    )
    await ctx.transition("hr", "interviews", interview["interview_id"], "COMPLETED")
    blocked = await ctx.client.post(path, headers=ctx.headers["lead"], json=hire_body)
    assert blocked.status_code == 409, blocked.text
    await configure(ctx, "RECRUITMENT")
    blocked = await ctx.client.post(path, headers=ctx.headers["lead"], json=hire_body)
    assert blocked.status_code == 409, blocked.text
    need = await submit(ctx, "RECRUITMENT", recruitment["recruitment_id"])
    assert [step["code"] for step in need["steps"]] == [
        "DIVISION_NEED_REVIEW",
        "HR_RECRUITMENT_REVIEW",
    ]
    assert need["packet"]["headcount"] == 1
    await action(ctx, need, "lead", 0, 403)
    need = await action(ctx, need, "workspace", 0)
    need = await action(ctx, need, "lead", 1)
    assert need["status"] == "COMPLETED"
    hired = await ctx.client.post(path, headers=ctx.headers["lead"], json=hire_body)
    assert hired.status_code == 200, hired.text
    employee = hired.json()
    replay = await ctx.client.post(path, headers=ctx.headers["lead"], json=hire_body)
    assert replay.json()["employee_id"] == employee["employee_id"]
    facility = await ctx.create(
        "hr",
        "facility-requests",
        {
            "facility_code": "OPS",
            "title": "Kesiapan tempat kerja",
            "resolution_notes": "Tempat kerja siap",
        },
    )
    onboarding = await ctx.create(
        "hr",
        "onboardings",
        {
            "employee_id": employee["employee_id"],
            "start_date": today,
            "facility_request_id": facility["facility_request_id"],
        },
    )
    await ctx.transition("hr", "onboardings", onboarding["onboarding_id"], "IN_PROGRESS")
    await configure(ctx, "ONBOARDING")
    result = await ctx.client.post(
        "/api/v1/processes",
        headers=ctx.headers["lead"],
        json={
            "business_type": "ONBOARDING",
            "subject_id": onboarding["onboarding_id"],
            "reason": "Persiapan bekerja",
        },
    )
    assert result.status_code == 201, result.text
    process = await action(ctx, result.json(), "lead", 0)
    await action(ctx, process, "identity", 1, 409)
    tokens: dict[str, str] = {}
    ctx.app.state.auth_service._activation_sink = lambda email, token: tokens.__setitem__(
        email, token
    )
    provisioned = await ctx.client.post(
        "/api/v1/identity/accounts",
        headers=identity_headers,
        json={
            "employee_id": employee["employee_id"],
            "workspace_id": "workspace_else",
            "role_refs": ["DIVISION_MEMBER"],
            "effective_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat(),
        },
    )
    assert provisioned.status_code == 201, provisioned.text
    actor_id = provisioned.json()["actor_id"]
    await action(ctx, process, "identity", 1, 409)
    activated = await ctx.client.post(
        "/api/v1/identity/activate",
        json={
            "token": tokens["hired@business.test"],
            "password": "EmployeePass!123",
            "password_confirmation": "EmployeePass!123",
        },
    )
    assert activated.status_code == 200, activated.text
    process = await action(ctx, process, "identity", 1)
    task_path = (
        f"/api/v1/processes/{process['process_id']}/steps/{process['steps'][2]['step_id']}/task"
    )
    task = await ctx.client.post(
        task_path,
        headers=ctx.headers["lead"],
        json={"reason": "Siapkan fasilitas karyawan melalui tugas GA"},
    )
    assert task.status_code == 201, task.text
    repeated_task = await ctx.client.post(
        task_path, headers=ctx.headers["lead"], json={"reason": "Periksa tugas yang sama"}
    )
    assert repeated_task.json()["task_id"] == task.json()["task_id"]
    await action(ctx, process, "lead", 2, 409)
    await ctx.transition("hr", "facility-requests", facility["facility_request_id"], "IN_PROGRESS")
    await ctx.transition("hr", "facility-requests", facility["facility_request_id"], "COMPLETED")
    await action(ctx, process, "lead", 2, 409)
    completed_task = await ctx.client.post(
        f"/api/v1/tasks/{task.json()['task_id']}/complete", headers=ctx.headers["lead"]
    )
    assert completed_task.status_code == 200, completed_task.text
    process = await action(ctx, process, "lead", 2)
    process = await action(ctx, process, "workspace", 3)
    assert process["status"] == "COMPLETED"
    await ctx.transition("hr", "onboardings", onboarding["onboarding_id"], "COMPLETED")
    login = await ctx.client.post(
        "/api/v1/auth/login", json={"email": "hired@business.test", "password": "EmployeePass!123"}
    )
    assert login.status_code == 200, login.text
    employee_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    inventory = await ctx.create(
        "hr",
        "inventory-items",
        {
            "asset_code": uuid4().hex,
            "name": "Laptop kerja",
            "condition": "GOOD",
            "recorded_on": today,
        },
    )
    handover: dict[str, Any] = {
        "inventory_item_id": inventory["inventory_item_id"],
        "employee_id": employee["employee_id"],
        "handover_on": today,
        "notes": "Serah terima tercatat oleh HR dan GA",
    }
    await ctx.create("hr", "asset-handovers", {**handover, "event": "GIVEN"})
    await configure(ctx, "OFFBOARDING", {"finance_settlement_required": True})
    result = await ctx.client.post(
        "/api/v1/processes",
        headers=ctx.headers["lead"],
        json={
            "business_type": "OFFBOARDING",
            "subject_id": employee["employee_id"],
            "reason": "Pengakhiran hubungan kerja",
        },
    )
    assert result.status_code == 201, result.text
    process = await action(ctx, result.json(), "workspace", 0)
    await action(ctx, process, "lead", 1, 409)
    await ctx.create("hr", "asset-handovers", {**handover, "event": "RETURNED"})
    process = await action(ctx, process, "lead", 1)
    process = await action(ctx, process, "workspace", 2)
    await action(ctx, process, "identity", 3, 409)
    await ctx.transition("hr", "employees", employee["employee_id"], "INACTIVE", 409)
    suspended = await ctx.client.post(
        f"/api/v1/identity/actors/{actor_id}/suspend",
        headers=identity_headers,
        json={"reason": "Pengakhiran hubungan kerja"},
    )
    assert suspended.status_code == 200, suspended.text
    revoked = await ctx.client.delete(
        f"/api/v1/identity/actors/{actor_id}/memberships/workspace_else", headers=identity_headers
    )
    assert revoked.status_code == 204, revoked.text
    process = await action(ctx, process, "identity", 3)
    assert process["status"] == "COMPLETED"
    await ctx.create("hr", "asset-handovers", {**handover, "event": "GIVEN"})
    await ctx.transition("hr", "employees", employee["employee_id"], "INACTIVE", 409)
    await ctx.create("hr", "asset-handovers", {**handover, "event": "RETURNED"})
    inactive = await ctx.transition("hr", "employees", employee["employee_id"], "INACTIVE")
    assert inactive["employment_status"] == "INACTIVE"
    old_session = await ctx.client.get("/api/v1/auth/whoami", headers=employee_headers)
    assert old_session.status_code == 401, old_session.text
    denied_login = await ctx.client.post(
        "/api/v1/auth/login", json={"email": "hired@business.test", "password": "EmployeePass!123"}
    )
    assert denied_login.status_code == 401, denied_login.text
