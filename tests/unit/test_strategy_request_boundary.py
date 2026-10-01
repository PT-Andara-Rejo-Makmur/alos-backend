"""HTTP contract validation fails closed before Strategy mutations."""

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from alos.config import Settings
from alos.dependencies import get_current_principal
from alos.domains.strategy.models import Period, PlanningAssumption, ScopeRef, VerificationState
from alos.identity import Principal
from alos.main import create_app

CONTRACTS = Path(__file__).resolve().parents[3] / "alos-contracts"
REQUESTS = [
    ("POST", "/plans", "strategy-plan-create-request.schema.json"),
    ("PATCH", "/plans/plan_01", "strategy-plan-update-request.schema.json"),
    ("POST", "/objectives", "strategic-objective-create-request.schema.json"),
    ("POST", "/targets", "business-target-create-request.schema.json"),
    ("POST", "/targets/target_01/observations", "metric-observation-create-request.schema.json"),
    ("POST", "/assumptions", "planning-assumption-create-request.schema.json"),
    ("POST", "/targets/target_01/relationships", "target-relationship-create-request.schema.json"),
    ("POST", "/cascade/preview", "cascade-preview-request.schema.json"),
    ("POST", "/cascade-runs/run_01/accept", "cascade-accept-request.schema.json"),
    ("POST", "/targets/target_01/revisions", "target-revision-create-request.schema.json"),
]


@pytest.fixture
def application():
    app = create_app(Settings(_env_file=None, APP_ENV="test", ALOS_CONTRACTS_PATH=CONTRACTS))
    actor = Principal(
        "actor_01",
        "tenant_01",
        "org_01",
        "workspace_01",
        permissions=frozenset({"strategy.read", "strategy.company.manage"}),
        roles=frozenset({"EXECUTIVE"}),
    )
    app.dependency_overrides[get_current_principal] = lambda: actor
    app.state.strategy_service = AsyncMock()
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(("method", "path", "schema"), REQUESTS)
@pytest.mark.parametrize("payload", [{"unsupported": "value"}, {"tenant_id": "tenant_other"}, []])
async def test_all_request_boundaries_reject_noncanonical_input(
    application, method, path, schema, payload
):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.request(method, "/api/v1/strategy" + path, json=payload)
    assert response.status_code == 422, response.text
    assert application.state.strategy_service.mock_calls == []
    documented = application.openapi()["paths"][
        "/api/v1/strategy"
        + path.replace("plan_01", "{plan_id}")
        .replace("target_01", "{target_id}")
        .replace("run_01", "{cascade_run_id}")
    ][method.lower()]
    assert documented["requestBody"]["content"]["application/json"]["schema"]["$ref"] == (
        "https://schemas.alos.dev/v1/strategy/" + schema
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"name": ""},
        {"period": {"granularity": "ANNUAL", "starts_at": "invalid", "ends_at": "2027-12-31"}},
        {"evidence_refs": ["evidence_01", "evidence_01"]},
        {"materiality": "UNKNOWN"},
        {"owner_role_ref": 123},
        {"lifecycle_state": "ACTIVE"},
    ],
)
async def test_update_validation_preserves_strict_fields_formats_and_uniqueness(
    application, payload
):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.patch("/api/v1/strategy/plans/plan_01", json=payload)
    assert response.status_code == 422, response.text
    assert application.state.strategy_service.mock_calls == []


@pytest.mark.asyncio
async def test_cascade_rule_cannot_claim_another_organization(application):
    payload = {
        "root_target_ref": {"target_id": "target_01", "version": 1},
        "rules": [
            {
                "cascade_rule_id": "rule_01",
                "tenant_id": "tenant_01",
                "organization_id": "org_other",
                "version": 1,
                "rule_type": "DIRECT",
                "input_target_refs": [{"target_id": "target_01", "version": 1}],
                "output_target_refs": [{"target_id": "target_02", "version": 1}],
                "parameters": {},
            }
        ],
        "rule_inputs": {"rule_01": {"value": None}},
        "assumption_refs": [],
        "constraints": [],
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.post("/api/v1/strategy/cascade/preview", json=payload)
    assert response.status_code == 403
    assert application.state.strategy_service.mock_calls == []


@pytest.mark.asyncio
async def test_missing_contract_artifact_fails_closed(application):
    application.state.settings.ALOS_CONTRACTS_PATH = None
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        response = await client.patch("/api/v1/strategy/plans/plan_01", json={"name": "Valid"})
    assert response.status_code == 503
    assert application.state.strategy_service.mock_calls == []


@pytest.mark.asyncio
async def test_assumption_reads_require_permission_and_filter_active_workspace():
    app = create_app(Settings(_env_file=None, APP_ENV="test", ALOS_CONTRACTS_PATH=CONTRACTS))
    member = Principal(
        "actor_01",
        "tenant_01",
        "org_01",
        "workspace_01",
        permissions=frozenset({"strategy.read"}),
        roles=frozenset({"DIVISION_MEMBER"}),
    )
    assumption = PlanningAssumption(
        "assumption_01",
        1,
        "Planning evidence",
        "CUSTOM",
        Decimal("1"),
        "COUNT",
        Period("ANNUAL", "2027-01-01", "2027-12-31"),
        ScopeRef("DIVISION", "workspace_01"),
        "source_01",
        "SOURCE_LINKED",
        (),
        VerificationState.UNVERIFIED,
        "DIVISION_LEAD",
        "workspace_01",
        "tenant_01",
        "org_01",
        "actor_01",
        "corr_01",
    )
    for row in (
        assumption,
        replace(assumption, assumption_id="assumption_other", owner_workspace_id="workspace_other"),
        replace(assumption, assumption_id="assumption_company", scope=ScopeRef("COMPANY")),
        replace(assumption, assumption_id="assumption_org", organization_id="org_other"),
        replace(assumption, assumption_id="assumption_tenant", tenant_id="tenant_other"),
    ):
        await app.state.strategy_repository.save_assumption(row)
    app.dependency_overrides[get_current_principal] = lambda: member
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/strategy/assumptions")
        assert response.status_code == 200
        assert {item["assumption_id"] for item in response.json()} == {
            "assumption_01",
            "assumption_company",
        }
        app.dependency_overrides[get_current_principal] = lambda: replace(
            member, permissions=frozenset()
        )
        assert (await client.get("/api/v1/strategy/assumptions")).status_code == 403
