"""Internal GA and distinct Legal review/revision evidence on migrated PostgreSQL."""

from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from sqlalchemy.exc import OperationalError
from test_business_domains import Context
from test_business_domains import context as migrated_context
from test_documents_api import _source
from test_strategy_planning_e2e import _asyncpg_url, _database_url

pytestmark = pytest.mark.asyncio(loop_scope="module")
context = migrated_context


async def check_scope(ctx: Context, domain: str, resource: str, identity: str) -> None:
    path = f"/api/v1/{domain}/{resource}/{identity}"
    for user in ["workspace", "org", "tenant"]:
        assert (await ctx.client.get(path, headers=ctx.headers[user])).status_code == 404
    for user in ["none", "it", "executive"]:
        assert (await ctx.client.get(path, headers=ctx.headers[user])).status_code == 403
    assert (await ctx.client.delete(path, headers=ctx.headers["lead"])).status_code == 405


async def test_ga_records_references_immutability_and_atomicity(
    context: Context, monkeypatch: pytest.MonkeyPatch
) -> None:
    employee = await context.create(
        "hr", "employees", {"employee_number": uuid4().hex, "full_name": "GA recipient"}
    )
    inventory = await context.create(
        "hr",
        "inventory-items",
        {
            "asset_code": uuid4().hex,
            "name": "Recorded item",
            "condition": "UNKNOWN",
            "recorded_on": "2026-01-01",
        },
    )
    facility = await context.create(
        "hr", "facility-requests", {"facility_code": "OFFICE", "title": "Repair request"}
    )
    await context.transition(
        "hr", "facility-requests", facility["facility_request_id"], "IN_PROGRESS"
    )
    await context.transition(
        "hr", "facility-requests", facility["facility_request_id"], "COMPLETED", 409
    )
    response = await context.client.patch(
        f"/api/v1/hr/facility-requests/{facility['facility_request_id']}",
        headers=context.headers["member"],
        json={"resolution_notes": "Human verified recorded work"},
    )
    assert response.status_code == 200
    await context.transition(
        "hr", "facility-requests", facility["facility_request_id"], "COMPLETED"
    )
    await check_scope(context, "hr", "facility-requests", facility["facility_request_id"])
    fixtures = [
        (
            "inventory-items",
            "inventory_item_id",
            {
                "asset_code": uuid4().hex,
                "name": "Empty condition source",
                "condition": "UNKNOWN",
                "recorded_on": "2026-01-01",
            },
        ),
        (
            "asset-handovers",
            "asset_handover_id",
            {
                "inventory_item_id": inventory["inventory_item_id"],
                "employee_id": employee["employee_id"],
                "handover_on": "2026-01-01",
                "event": "GIVEN",
                "notes": "Recorded human handover",
            },
        ),
        (
            "maintenance-records",
            "maintenance_record_id",
            {
                "inventory_item_id": inventory["inventory_item_id"],
                "performed_on": "2026-01-01",
                "summary": "Inspection",
                "result": "UNRESOLVED",
            },
        ),
        (
            "service-assessments",
            "service_assessment_id",
            {
                "facility_code": "OFFICE",
                "assessed_on": "2026-01-01",
                "readiness": "UNKNOWN",
                "notes": "No verified assessment available",
            },
        ),
    ]
    for resource, identifier, values in fixtures:
        row = await context.create("hr", resource, values)
        await check_scope(context, "hr", resource, row[identifier])
        assert row["allowed_transitions"] == []
        assert (
            await context.client.patch(
                f"/api/v1/hr/{resource}/{row[identifier]}",
                headers=context.headers["lead"],
                json=values,
            )
        ).status_code == 405
        assert (
            await context.client.post(
                f"/api/v1/hr/{resource}", headers=context.headers["workspace"], json=values
            )
        ).status_code == (404 if "inventory_item_id" in values else 201)
    # Readiness evidence never changes inventory condition, identity or employee employment.
    assert (
        await context.client.get(
            f"/api/v1/hr/inventory-items/{inventory['inventory_item_id']}",
            headers=context.headers["member"],
        )
    ).json()["condition"] == "UNKNOWN"
    repo = context.app.state.hr_service.repository

    async def fail(*args: Any, **kwargs: Any) -> None:
        raise OperationalError("audit unavailable", {}, RuntimeError("test rollback"))

    monkeypatch.setattr(repo.audit, "append_in_session", fail)
    before = await context.audit_count("hr")
    response = await context.client.post(
        "/api/v1/hr/inventory-items",
        headers=context.headers["member"],
        json={**fixtures[0][2], "asset_code": uuid4().hex},
    )
    assert response.status_code == 503
    assert await context.audit_count("hr") == before


async def test_legal_review_distinct_from_execution_and_immutable_revision(
    context: Context,
) -> None:
    contract = await context.create(
        "legal",
        "contracts",
        {
            "contract_number": uuid4().hex,
            "contract_type": "SERVICE",
            "counterparty_name": "Internal reference",
        },
    )
    review = await context.create(
        "legal",
        "legal-reviews",
        {"contract_id": contract["contract_id"], "title": "Legal assessment"},
    )
    await check_scope(context, "legal", "legal-reviews", review["legal_review_id"])
    await context.transition("legal", "legal-reviews", review["legal_review_id"], "IN_REVIEW")
    await context.transition("legal", "legal-reviews", review["legal_review_id"], "REVIEWED", 409)
    response = await context.client.patch(
        f"/api/v1/legal/legal-reviews/{review['legal_review_id']}",
        headers=context.headers["member"],
        json={"review_summary": "Explicit assessment only", "assessment": "INCONCLUSIVE"},
    )
    assert response.status_code == 200
    await context.transition(
        "legal", "legal-reviews", review["legal_review_id"], "REVIEWED", 403, "member"
    )
    reviewed = await context.transition(
        "legal", "legal-reviews", review["legal_review_id"], "REVIEWED"
    )
    assert reviewed["reviewed_by"] and reviewed["reviewed_at"]
    document = await context.client.post(
        "/api/v1/documents",
        headers=context.headers["lead"],
        json={
            "title": "Immutable revision document",
            "category": "GENERAL",
            "data_classification": "INTERNAL",
        },
    )
    assert document.status_code == 201
    did = document.json()["document_id"]
    source_id = uuid4().hex
    connection = await asyncpg.connect(_asyncpg_url(_database_url("alos_business_domains_test")))
    try:
        await _source(
            connection,
            source_id=source_id,
            workspace_id="workspace_business",
            tenant_id="tenant_business",
            organization_id="org_business",
            document_id=did,
        )
    finally:
        await connection.close()
    version = await context.client.post(
        f"/api/v1/documents/{did}/versions",
        headers=context.headers["lead"],
        json={"version": "1.0", "source_id": source_id, "source_version": "1"},
    )
    assert version.status_code == 201, version.text
    values = {
        "contract_id": contract["contract_id"],
        "document_id": did,
        "document_version": "1.0",
        "revision_number": 1,
        "summary": "Recorded amendment reference",
        "recorded_on": "2026-01-01",
    }
    missing = await context.client.post(
        "/api/v1/legal/contract-revisions",
        headers=context.headers["member"],
        json={**values, "document_version": "missing"},
    )
    assert missing.status_code == 404
    revision = await context.create("legal", "contract-revisions", values)
    await check_scope(context, "legal", "contract-revisions", revision["contract_revision_id"])
    assert (
        await context.client.patch(
            f"/api/v1/legal/contract-revisions/{revision['contract_revision_id']}",
            headers=context.headers["lead"],
            json={"summary": "overwrite"},
        )
    ).status_code == 405
    assert (
        await context.client.post(
            "/api/v1/legal/contract-revisions", headers=context.headers["lead"], json=values
        )
    ).status_code == 409
    assert (
        await context.client.patch(
            f"/api/v1/legal/contracts/{contract['contract_id']}",
            headers=context.headers["lead"],
            json={"counterparty_name": "overwrite"},
        )
    ).status_code == 409
    unchanged = await context.client.get(
        f"/api/v1/legal/contracts/{contract['contract_id']}", headers=context.headers["member"]
    )
    assert unchanged.json()["status"] == "DRAFT"
