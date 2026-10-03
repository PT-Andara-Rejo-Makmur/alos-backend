"""Real migrated PostgreSQL proof of Legal, HR and IT ownership boundaries."""

from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from test_business_domains import Context
from test_business_domains import context as migrated_context

from alos.domains.hr.records import SPECS as HR
from alos.domains.it.records import SPECS as IT
from alos.domains.legal.records import SPECS as LEGAL
from alos.identity import DataScope, Principal
from alos.security.errors import PlatformError

pytestmark = pytest.mark.asyncio(loop_scope="module")
context = migrated_context
SPECS = {"legal": LEGAL, "hr": HR, "it": IT}
STAMP = "2026-01-01T08:00:00Z"
FINISH = "2026-01-01T09:00:00Z"


async def seeds(ctx: Context) -> dict[str, Any]:
    """Independent, explicitly recorded integration fixtures, never production data."""
    employee = await ctx.create(
        "hr",
        "employees",
        {
            "employee_number": uuid4().hex,
            "full_name": "Recorded employee",
        },
    )
    recruitment = await ctx.create(
        "hr",
        "recruitments",
        {
            "position_title": "Engineer",
            "opened_at": STAMP,
        },
    )
    candidate = await ctx.create(
        "hr",
        "candidates",
        {
            "full_name": "Recorded candidate",
            "recruitment_id": recruitment["recruitment_id"],
        },
    )
    await ctx.transition("hr", "candidates", candidate["candidate_id"], "SCREENING")
    training = await ctx.create("hr", "trainings", {"name": "Internal training"})
    succession = await ctx.create("hr", "successions", {"position_title": "Engineer"})
    contract = await ctx.create(
        "legal",
        "contracts",
        {
            "contract_number": uuid4().hex,
            "contract_type": "SERVICE",
            "counterparty_name": "Recorded counterparty",
        },
    )
    due = await ctx.create(
        "legal",
        "due-diligences",
        {
            "subject_type": "CONTRACT",
            "subject_id": contract["contract_id"],
            "title": "Review",
        },
    )
    system = await ctx.create(
        "it",
        "systems",
        {
            "system_code": uuid4().hex,
            "name": "Internal system",
            "criticality": "HIGH",
        },
    )
    repository = await ctx.create("it", "repositories", {"name": "Inventory"})
    environment = await ctx.create(
        "it",
        "environments",
        {
            "name": "Production inventory",
            "environment_type": "PRODUCTION",
        },
    )
    pipeline = await ctx.create(
        "it",
        "cicd-pipelines",
        {
            "repository_id": repository["repository_id"],
            "name": "CI inventory",
            "provider": "GITHUB",
        },
    )
    policy = await ctx.create(
        "it",
        "backup-policies",
        {
            "system_id": system["system_id"],
            "name": "Recorded policy",
            "frequency": "DAILY",
            "retention_days": 30,
        },
    )
    backup = await ctx.create(
        "it",
        "backup-runs",
        {
            "backup_policy_id": policy["backup_policy_id"],
            "started_at": STAMP,
            "finished_at": FINISH,
            "recorded_status": "SUCCEEDED",
            "artifact_ref": "evidence/backup",
        },
    )
    return {
        "employee_id": employee["employee_id"],
        "recruitment_id": recruitment["recruitment_id"],
        "candidate_id": candidate["candidate_id"],
        "training_id": training["training_id"],
        "succession_id": succession["succession_id"],
        "contract_id": contract["contract_id"],
        "due_diligence_id": due["due_diligence_id"],
        "system_id": system["system_id"],
        "repository_id": repository["repository_id"],
        "environment_id": environment["environment_id"],
        "pipeline_id": pipeline["pipeline_id"],
        "backup_policy_id": policy["backup_policy_id"],
        "backup_run_id": backup["backup_run_id"],
    }


def samples(ids: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    employee = {"employee_id": ids["employee_id"]}
    system = {"system_id": ids["system_id"]}
    subject = {"subject_type": "CONTRACT", "subject_id": ids["contract_id"]}
    return {
        "legal": {
            "permits": {"permit_type": "RECORDED", "subject": "Permit observation"},
            "contracts": {
                "contract_number": uuid4().hex,
                "contract_type": "SERVICE",
                "counterparty_name": "Counterparty",
            },
            "land_documents": {"document_type": "RECORDED", "property_ref": "opaque-source-ref"},
            "due_diligences": {**subject, "title": "Internal diligence"},
            "due_diligence_items": {
                "due_diligence_id": ids["due_diligence_id"],
                "item_type": "CHECK",
            },
            "cases": {
                "case_number": uuid4().hex,
                "case_type": "TRACKING",
                "title": "Internal case",
            },
            "claim_reviews": {**subject, "claim_amount": "123456789012345678.90"},
            "expiries": {**subject, "expires_at": "2027-01-01"},
            "privacy_requests": {"request_type": "ACCESS", "description": "Internal tracking"},
            "risks": {
                "title": "Explicit risk",
                "likelihood": "LOW",
                "impact": "HIGH",
                "rating": "MEDIUM",
            },
            "controls": {"control_code": uuid4().hex, "title": "Internal control"},
        },
        "hr": {
            "employees": {"employee_number": uuid4().hex, "full_name": "Employee"},
            "attendances": {
                **employee,
                "attendance_date": "2026-01-01",
                "recorded_status": "LATE",
                "check_in_at": STAMP,
                "check_out_at": FINISH,
                "source": "Operator record",
            },
            "leave_requests": {
                **employee,
                "leave_type": "ANNUAL",
                "start_date": "2027-01-01",
                "end_date": "2027-01-02",
            },
            "recruitments": {"position_title": "Recorded vacancy", "opened_at": STAMP},
            "candidates": {"full_name": "Candidate", "recruitment_id": ids["recruitment_id"]},
            "interviews": {
                "candidate_id": ids["candidate_id"],
                "scheduled_at": STAMP,
                "notes": "Recorded notes",
            },
            "onboardings": {**employee, "start_date": "2027-01-01"},
            "performance_reviews": {
                **employee,
                "review_period": "2026",
                "rating": "3.25",
                "summary": "Explicit review",
            },
            "trainings": {"name": "Internal training"},
            "training_enrollments": {**employee, "training_id": ids["training_id"]},
            "successions": {"position_title": "Recorded succession"},
            "succession_candidates": {
                **employee,
                "succession_id": ids["succession_id"],
                "readiness": "RECORDED",
            },
            "grievances": {
                **employee,
                "category": "INTERNAL",
                "description": "Private recorded grievance",
            },
            "employment_contracts": {
                **employee,
                "contract_number": uuid4().hex,
                "contract_type": "RECORDED",
                "start_date": "2027-01-01",
            },
            "personnel_files": {**employee, "file_type": "RECORDED"},
        },
        "it": {
            "systems": {"system_code": uuid4().hex, "name": "System", "criticality": "LOW"},
            "integrations": {
                **system,
                "name": "Inventory only",
                "integration_type": "METADATA",
                "endpoint_ref": "https://example.test/inventory",
            },
            "databases": {**system, "name": "Database inventory", "engine": "POSTGRES"},
            "environments": {"name": "Environment", "environment_type": "PRODUCTION"},
            "repositories": {"name": "Repository inventory"},
            "cicd_pipelines": {
                "repository_id": ids["repository_id"],
                "name": "Pipeline",
                "provider": "RECORDED",
            },
            "ci_runs": {
                "pipeline_id": ids["pipeline_id"],
                "recorded_status": "FAILED",
                "started_at": STAMP,
                "finished_at": FINISH,
            },
            "releases": {
                "repository_id": ids["repository_id"],
                "environment_id": ids["environment_id"],
                "version": "1.0.0",
            },
            "technical_debts": {
                "title": "Debt",
                "priority": "HIGH",
                "description": "Explicit notes",
            },
            "service_monitors": {
                **system,
                "name": "Recorded observation",
                "check_type": "OPERATOR",
                "target": "https://example.test/health",
                "recorded_status": "DOWN",
                "last_checked_at": STAMP,
            },
            "incidents": {
                **system,
                "title": "Incident",
                "severity": "HIGH",
                "description": "Explicit notes",
            },
            "security_findings": {
                **system,
                "title": "Finding",
                "severity": "CRITICAL",
                "description": "Explicit notes",
            },
            "backup_policies": {
                **system,
                "name": "Policy",
                "frequency": "DAILY",
                "retention_days": 30,
            },
            "backup_runs": {
                "backup_policy_id": ids["backup_policy_id"],
                "started_at": STAMP,
                "recorded_status": "FAILED",
                "finished_at": FINISH,
            },
            "restore_tests": {
                "backup_run_id": ids["backup_run_id"],
                "tested_at": FINISH,
                "result": "PASSED",
                "notes": "Explicit operator evidence",
            },
            "dr_plans": {**system, "name": "Recorded plan", "rto_minutes": 30, "rpo_minutes": 15},
        },
    }


@pytest.mark.parametrize("domain", ["legal", "hr", "it"])
async def test_every_resource_roundtrip_and_scope(context: Context, domain: str) -> None:
    fixtures = samples(await seeds(context))[domain]
    # New internal records have separate roundtrip and reference tests.
    for resource in fixtures:
        spec = SPECS[domain][resource]
        path = resource.replace("_", "-")
        values = fixtures[resource]
        row = await context.create(domain, path, values, user="member")
        for key, value in values.items():
            actual = "status" if key == "recorded_status" else key
            assert (
                row[actual] == value.replace("Z", "+00:00")
                if isinstance(value, str) and value.endswith("Z")
                else row[actual] == value
            )
        assert row["tenant_id"] == "tenant_business"
        identity = row[spec.identifier]
        for user in ("workspace", "org", "tenant"):
            read = await context.client.get(
                f"/api/v1/{domain}/{path}/{identity}", headers=context.headers[user]
            )
            assert read.status_code == 404, read.text
        assert (
            await context.client.delete(
                f"/api/v1/{domain}/{path}/{identity}", headers=context.headers["lead"]
            )
        ).status_code == 405
        if spec.immutable:
            response = await context.client.patch(
                f"/api/v1/{domain}/{path}/{identity}", headers=context.headers["lead"], json=values
            )
            assert response.status_code == 405
        if not spec.transitions:
            response = await context.client.post(
                f"/api/v1/{domain}/{path}/{identity}/transition",
                headers=context.headers["lead"],
                json={"status": "CLOSED"},
            )
            assert response.status_code == 404


@pytest.mark.parametrize(
    "domain,resource,values",
    [
        (
            "legal",
            "contracts",
            {
                "contract_number": "AUTH",
                "contract_type": "SERVICE",
                "counterparty_name": "Recorded",
            },
        ),
        ("hr", "employees", {"employee_number": "AUTH", "full_name": "Recorded employee"}),
        ("it", "systems", {"system_code": "AUTH", "name": "System", "criticality": "HIGH"}),
    ],
)
async def test_authority_forgery_generic_and_unknown(
    context: Context, domain: str, resource: str, values: dict[str, Any]
) -> None:
    spec = SPECS[domain][resource]
    for user in ("executive", "none", *(("it",) if domain != "it" else ())):
        response = await context.client.post(
            f"/api/v1/{domain}/{resource}", headers=context.headers[user], json=values
        )
        assert response.status_code == 403
    for field in (
        "tenant_id",
        "organization_id",
        "workspace_id",
        "created_at",
        "updated_at",
        "actor_id",
        "approved_by",
        "approved_at",
        "owner_actor_id",
        "decided_by",
        "verified_by",
        spec.status_field,
    ):
        response = await context.client.post(
            f"/api/v1/{domain}/{resource}",
            headers=context.headers["lead"],
            json={**values, field: "forged"},
        )
        assert response.status_code == 422
    row = await context.create(domain, resource, values)
    if domain == "it":
        allowed = await context.client.patch(
            f"/api/v1/it/systems/{row[spec.identifier]}",
            headers=context.headers["it"],
            json={"name": "IT operator"},
        )
        assert allowed.status_code == 403
    for method in ("post", "patch", "delete"):
        suffix = "" if method == "post" else "/" + row[spec.identifier]
        response = await context.client.request(
            method.upper(),
            f"/api/v1/domains/{domain}/{resource}{suffix}",
            headers=context.headers["lead"],
            json=values if method != "delete" else None,
        )
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "CANONICAL_DOMAIN_MUTATION_REQUIRED"
    repository = getattr(context.app.state, f"{domain}_service").repository
    async with repository.factory() as session, session.begin():
        table = await repository.table(session, domain, resource)
        await session.execute(
            update(table)
            .where(table.c[spec.identifier] == row[spec.identifier])
            .values({spec.status_field: "Legacy-Exact"})
        )
    read = await context.client.get(
        f"/api/v1/{domain}/{resource}/{row[spec.identifier]}", headers=context.headers["lead"]
    )
    assert read.json()[spec.status_field] == "Legacy-Exact"
    assert read.json()["allowed_transitions"] == []
    mutate = await context.client.patch(
        f"/api/v1/{domain}/{resource}/{row[spec.identifier]}",
        headers=context.headers["lead"],
        json=values,
    )
    assert mutate.status_code == 409


async def test_references_material_decisions_and_employee_account_separation(
    context: Context,
) -> None:
    ids = await seeds(context)
    fixtures = samples(ids)
    # Count actual Identity actors before employee changes: HR must not provision or revoke.
    records = context.app.state.hr_service.repository
    async with records.factory() as session:
        actors = await records.table(session, "core", "actors")
        before = await session.scalar(select(func.count()).select_from(actors))
    employee = await context.create(
        "hr", "employees", {"employee_number": uuid4().hex, "full_name": "No account"}
    )
    assert employee["actor_id"] is None
    await context.transition("hr", "employees", employee["employee_id"], "INACTIVE", 403, "member")
    await context.transition("hr", "employees", employee["employee_id"], "INACTIVE")
    async with records.factory() as session:
        assert await session.scalar(select(func.count()).select_from(actors)) == before
    token = context.headers["lead"]["Authorization"].removeprefix("Bearer ")
    account_before = await context.app.state.auth_service.whoami(token)
    associated = await context.create(
        "hr", "employees", {"employee_number": uuid4().hex, "full_name": "Associated employee"}
    )
    async with records.factory() as session, session.begin():
        table = await records.table(session, "hr", "employees")
        await session.execute(
            update(table)
            .where(table.c.employee_id == associated["employee_id"])
            .values(actor_id=account_before["actor"]["actor_id"])
        )
    await context.transition("hr", "employees", associated["employee_id"], "INACTIVE", 409)
    unchanged = await context.client.get(
        f"/api/v1/hr/employees/{associated['employee_id']}", headers=context.headers["lead"]
    )
    assert unchanged.json()["employment_status"] == "ACTIVE"
    account_after = await context.app.state.auth_service.whoami(token)
    assert account_after["actor"] == account_before["actor"]
    assert account_after["active_workspace"] == account_before["active_workspace"]
    for domain, resource, field in (
        ("hr", "attendances", "employee_id"),
        ("hr", "candidates", "recruitment_id"),
        ("hr", "interviews", "candidate_id"),
        ("hr", "grievances", "employee_id"),
        ("legal", "due_diligence_items", "due_diligence_id"),
        ("legal", "due_diligences", "subject_id"),
        *(
            ("it", resource, "system_id")
            for resource in ("databases", "integrations", "service_monitors", "dr_plans")
        ),
    ):
        path = resource.replace("_", "-")
        for user in ("workspace", "org", "tenant"):
            result = await context.client.post(
                f"/api/v1/{domain}/{path}",
                headers=context.headers[user],
                json=fixtures[domain][resource],
            )
            assert result.status_code == 404, (field, result.text)
    for domain, resource, final in (
        ("legal", "contracts", "SIGNED"),
        ("legal", "permits", "VALID"),
        ("legal", "due_diligences", "APPROVED"),
        ("legal", "cases", "WON"),
        ("hr", "employment_contracts", "SIGNED"),
        ("it", "releases", "RELEASED"),
    ):
        path = resource.replace("_", "-")
        row = await context.create(domain, path, fixtures[domain][resource])
        for role in ("member", "lead"):
            await context.transition(
                domain, path, row[SPECS[domain][resource].identifier], final, 422, role
            )
    for key in ("api_key", "password", "secret", "credentials", "connector_config"):
        response = await context.client.post(
            "/api/v1/it/integrations",
            headers=context.headers["lead"],
            json={**fixtures["it"]["integrations"], key: "forged"},
        )
        assert response.status_code == 422
    assert (
        await context.client.post(
            "/api/v1/it/integrations",
            headers=context.headers["lead"],
            json={
                **fixtures["it"]["integrations"],
                "endpoint_ref": "https://user:secret@example.test",
            },
        )
    ).status_code == 422


async def test_document_port_and_internal_lifecycle(context: Context) -> None:
    document = await context.client.post(
        "/api/v1/documents",
        headers=context.headers["lead"],
        json={
            "title": "Recorded document",
            "category": "GENERAL",
            "data_classification": "INTERNAL",
        },
    )
    assert document.status_code == 201, document.text
    did = document.json()["document_id"]
    ids = await seeds(context)
    for domain, resource in (
        ("legal", "contracts"),
        ("legal", "land_documents"),
        ("hr", "employment_contracts"),
        ("hr", "personnel_files"),
    ):
        values = {**samples(ids)[domain][resource], "document_id": did}
        row = await context.create(domain, resource.replace("_", "-"), values)
        assert row["document_id"] == did
        for user in ("workspace", "org", "tenant"):
            values = {**values, "employee_id": None} if domain == "hr" else values
            if domain == "hr":
                other_employee = await context.create(
                    "hr", "employees", {"employee_number": uuid4().hex, "full_name": "Other"}, user
                )
                values["employee_id"] = other_employee["employee_id"]
            denied = await context.client.post(
                f"/api/v1/{domain}/{resource.replace('_', '-')}",
                headers=context.headers[user],
                json=values,
            )
            assert denied.status_code == 404, denied.text
    contract = await context.create("legal", "contracts", samples(ids)["legal"]["contracts"])
    await context.transition(
        "legal", "contracts", contract["contract_id"], "IN_REVIEW", user="member"
    )
    risk = await context.create("legal", "risks", samples(ids)["legal"]["risks"])
    await context.transition("legal", "risks", risk["risk_id"], "MITIGATING", user="member")
    await context.transition("legal", "risks", risk["risk_id"], "REVIEWED", 403, "member")
    await context.transition("legal", "risks", risk["risk_id"], "REVIEWED")
    for resource, progress in (
        ("incidents", ("INVESTIGATING", "RESOLVED")),
        ("security_findings", ("IN_REVIEW", "REMEDIATING", "RESOLVED")),
    ):
        row = await context.create("it", resource.replace("_", "-"), samples(ids)["it"][resource])
        for state in progress:
            await context.transition(
                "it", resource.replace("_", "-"), row[IT[resource].identifier], state, user="member"
            )
        read = await context.client.get(
            f"/api/v1/it/{resource.replace('_', '-')}/{row[IT[resource].identifier]}",
            headers=context.headers["member"],
        )
        assert read.json()["allowed_transitions"] == []
        await context.transition(
            "it", resource.replace("_", "-"), row[IT[resource].identifier], "CLOSED", 403, "member"
        )
        await context.transition(
            "it", resource.replace("_", "-"), row[IT[resource].identifier], "CLOSED"
        )


async def test_it_operations_admin_has_only_its_domain_authority(context: Context) -> None:
    email = "it-operations@operations.test"
    registration = await context.client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass!123",
            "display_name": "IT Operations",
            "tenant_id": "tenant_business",
            "organization_id": "org_business",
            "workspace_id": "workspace_it_operations",
            "workspace_key": "it-operations",
            "workspace_name": "IT Operations",
            "workspace_type": "IT_OPERATIONS",
            "division_code": "IT",
            "role_refs": ["IT_ADMIN"],
            "data_scope": "WORKSPACE",
        },
    )
    assert registration.status_code == 201, registration.text
    login = await context.client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "StrongPass!123"},
    )
    headers = {"Authorization": "Bearer " + login.json()["access_token"]}
    created = await context.client.post(
        "/api/v1/it/systems",
        headers=headers,
        json={"system_code": "ADMIN", "name": "Inventory", "criticality": "HIGH"},
    )
    assert created.status_code == 201, created.text
    for domain, resource, values in (
        ("hr", "employees", {"employee_number": "DENIED", "full_name": "No privilege"}),
        (
            "legal",
            "contracts",
            {
                "contract_number": "DENIED",
                "contract_type": "SERVICE",
                "counterparty_name": "No privilege",
            },
        ),
    ):
        denied = await context.client.post(
            f"/api/v1/{domain}/{resource}", headers=headers, json=values
        )
        assert denied.status_code == 403


@pytest.mark.parametrize("domain", ["legal", "hr", "it"])
async def test_audit_append_failure_rolls_back_business_and_audit(
    context: Context, domain: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = getattr(context.app.state, f"{domain}_service").repository
    resource = {"legal": "contracts", "hr": "employees", "it": "systems"}[domain]
    values = samples(await seeds(context))[domain][resource]
    path = f"/api/v1/{domain}/{resource}"
    before = (await context.client.get(path, headers=context.headers["lead"])).json()["total"]
    audits = await context.audit_count(domain)
    original = repository.audit.append_in_session

    async def fail_after_append(*args: Any, **kwargs: Any) -> None:
        await original(*args, **kwargs)
        raise OperationalError("injected audit failure", {}, Exception("test failure"))

    monkeypatch.setattr(repository.audit, "append_in_session", fail_after_append)
    response = await context.client.post(path, headers=context.headers["lead"], json=values)
    assert response.status_code == 503
    monkeypatch.setattr(repository.audit, "append_in_session", original)
    assert (await context.client.get(path, headers=context.headers["lead"])).json()[
        "total"
    ] == before
    assert await context.audit_count(domain) == audits


@pytest.mark.parametrize("domain", ["legal", "hr", "it"])
async def test_executive_company_partial_failure_and_security_fail_closed(
    context: Context, domain: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await seeds(context)
    service = getattr(context.app.state, f"{domain}_service")
    who = await context.app.state.auth_service.whoami(
        context.headers["executive"]["Authorization"].removeprefix("Bearer ")
    )
    access = who["active_workspace"]
    principal = Principal(
        actor_id=who["actor"]["actor_id"],
        tenant_id=who["actor"]["tenant_id"],
        organization_id=who["actor"]["organization_id"],
        workspace_id=access["workspace"]["workspace_id"],
        roles=frozenset(access["role_refs"]),
        permissions=frozenset(access["permission_refs"]),
        scopes=frozenset(access["scope_refs"]),
        data_scope=DataScope.COMPANY,
    )
    company = await service.overview(principal, executive=True)
    assert company["source"]["status"] == "CONNECTED"
    scoped = await service.overview(
        replace(principal, data_scope=DataScope.WORKSPACE), executive=True
    )
    assert scoped["source"]["status"] == "CONNECTED_EMPTY"
    assert not scoped["counts"][next(iter(SPECS[domain]))]
    assert ids

    async def fail(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise OperationalError("source failure", {}, Exception("test failure"))

    monkeypatch.setattr(service, "overview", fail)
    response = await context.client.get(
        "/api/v1/executive/overview", headers=context.headers["executive"]
    )
    assert response.status_code == 200, response.text
    statuses = {item["domain"]: item["status"] for item in response.json()["domains"]}
    assert statuses[domain.upper()] == "ERROR"
    assert all(
        value in {"CONNECTED", "CONNECTED_EMPTY"}
        for key, value in statuses.items()
        if key != domain.upper()
    )

    async def deny(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise PlatformError("CONTRACTS_UNAVAILABLE", "fail closed", status_code=503)

    monkeypatch.setattr(service, "overview", deny)
    response = await context.client.get(
        "/api/v1/executive/overview", headers=context.headers["executive"]
    )
    assert response.status_code == 503
    assert response.json()["code"] == "CONTRACTS_UNAVAILABLE"
