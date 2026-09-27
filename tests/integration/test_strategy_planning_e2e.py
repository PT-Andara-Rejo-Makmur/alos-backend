"""Persistent Strategy API proof against a migrated PostgreSQL database."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select

from alos.config import Settings
from alos.domains.strategy.models import Constraint
from alos.identity import Principal
from alos.main import create_app
from alos.persistence.models import AuditRecord
from alos.persistence.strategy_models import StrategyTargetRecord

pytestmark = pytest.mark.asyncio(loop_scope="module")

DATABASE_NAME = "alos_strategy_e2e"
PASSWORD = "StrongPass!123"  # noqa: S105 - isolated integration-test credential
PERIOD = {"granularity": "ANNUAL", "starts_at": "2027-01-01", "ends_at": "2027-12-31"}


def _database_url(name: str) -> str:
    source = os.environ.get(
        "ALOS_TEST_DATABASE_URL",
        "postgresql+asyncpg://alos:alos@127.0.0.1:5432/alos_test",
    )
    parts = urlsplit(source)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


def _asyncpg_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _recreate_database() -> str:
    admin = await asyncpg.connect(_asyncpg_url(_database_url("postgres")))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{DATABASE_NAME}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{DATABASE_NAME}"')
    finally:
        await admin.close()
    url = _database_url(DATABASE_NAME)
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
    return url


@dataclass(frozen=True)
class StrategyContext:
    client: httpx.AsyncClient
    app: FastAPI
    executive_headers: dict[str, str]
    sales_headers: dict[str, str]
    unrelated_headers: dict[str, str]
    cross_org_headers: dict[str, str]
    cross_tenant_headers: dict[str, str]
    missing_permission_headers: dict[str, str]
    executive: Principal


async def _register_and_login(
    client: httpx.AsyncClient,
    *,
    email: str,
    tenant_id: str,
    organization_id: str,
    workspace_id: str,
    roles: list[str],
    permissions: list[str],
) -> tuple[dict[str, str], str]:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "display_name": email.split("@", 1)[0],
            "tenant_id": tenant_id,
            "organization_id": organization_id,
            "workspace_id": workspace_id,
            "workspace_key": workspace_id,
            "workspace_name": workspace_id,
            "workspace_type": "EXECUTIVE" if "EXECUTIVE" in roles else "BUSINESS",
            "role_refs": roles,
            "permission_refs": permissions,
            "scope_refs": ["scope.strategy"],
            "data_scope": "COMPANY" if "EXECUTIVE" in roles else "WORKSPACE",
        },
    )
    assert registration.status_code == 201, registration.text
    actor_id = str(registration.json()["actor"]["actor_id"])
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return {"Authorization": f"Bearer {login.json()['access_token']}"}, actor_id


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def strategy_context() -> AsyncIterator[StrategyContext]:
    url = await _recreate_database()
    settings = Settings(
        _env_file=None,
        APP_ENV="development",
        DATABASE_URL=url,
        GENESIS_BASE_URL="http://genesis.invalid",
        GENESIS_INTERNAL_TOKEN="test-only-token",  # noqa: S106
        OTEL_SERVICE_NAME="alos-strategy-e2e",
        ENABLE_TEST_REGISTRATION=True,
    )
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        strategy_permissions = [
            "strategy.read",
            "strategy.company.manage",
            "strategy.review",
            "strategy.approve",
            "strategy.activate",
        ]
        executive_headers, actor_id = await _register_and_login(
            client,
            email="strategy-executive@e2e.local",
            tenant_id="tenant_strategy_e2e",
            organization_id="org_strategy_e2e",
            workspace_id="workspace_strategy_executive_e2e",
            roles=["EXECUTIVE", "BUSINESS_REVIEWER"],
            permissions=strategy_permissions,
        )
        sales_headers, _ = await _register_and_login(
            client,
            email="strategy-sales@e2e.local",
            tenant_id="tenant_strategy_e2e",
            organization_id="org_strategy_e2e",
            workspace_id="workspace_strategy_sales_e2e",
            roles=["WORKSPACE_LEAD"],
            permissions=["strategy.read", "strategy.division.manage"],
        )
        unrelated_headers, _ = await _register_and_login(
            client,
            email="strategy-property@e2e.local",
            tenant_id="tenant_strategy_e2e",
            organization_id="org_strategy_e2e",
            workspace_id="workspace_strategy_property_e2e",
            roles=["WORKSPACE_LEAD"],
            permissions=["strategy.read", "strategy.division.manage"],
        )
        cross_org_headers, _ = await _register_and_login(
            client,
            email="strategy-other-org@e2e.local",
            tenant_id="tenant_strategy_e2e",
            organization_id="org_strategy_other",
            workspace_id="workspace_other_org",
            roles=["EXECUTIVE"],
            permissions=strategy_permissions,
        )
        cross_tenant_headers, _ = await _register_and_login(
            client,
            email="strategy-other-tenant@e2e.local",
            tenant_id="tenant_strategy_other",
            organization_id="org_strategy_other_tenant",
            workspace_id="workspace_other_tenant",
            roles=["EXECUTIVE"],
            permissions=strategy_permissions,
        )
        missing_permission_headers, _ = await _register_and_login(
            client,
            email="strategy-no-permission@e2e.local",
            tenant_id="tenant_strategy_e2e",
            organization_id="org_strategy_e2e",
            workspace_id="workspace_strategy_executive_e2e",
            roles=["EXECUTIVE"],
            permissions=[],
        )
        yield StrategyContext(
            client,
            app,
            executive_headers,
            sales_headers,
            unrelated_headers,
            cross_org_headers,
            cross_tenant_headers,
            missing_permission_headers,
            Principal(
                actor_id,
                "tenant_strategy_e2e",
                "org_strategy_e2e",
                "workspace_strategy_executive_e2e",
                permissions=frozenset(strategy_permissions),
                roles=frozenset({"EXECUTIVE", "BUSINESS_REVIEWER"}),
            ),
        )
    await app.state.database.dispose()


def _plan_payload(plan_id: str, *, evidence: bool = True) -> dict[str, Any]:
    return {
        "plan_id": plan_id,
        "version": 1,
        "plan_type": "OPERATING_PLAN",
        "name": f"RKAP persistent {plan_id}",
        "owner_workspace_id": "workspace_strategy_executive_e2e",
        "owner_role_ref": "EXECUTIVE",
        "period": PERIOD,
        "scope": {"type": "COMPANY", "ref": None},
        "materiality": "MATERIAL",
        "source_refs": ["source:board-approved-rkap"],
        "evidence_refs": ["evidence:board-approved-rkap"] if evidence else [],
    }


def _target_payload(
    target_id: str,
    plan_id: str,
    *,
    objective_id: str | None = None,
    workspace_id: str = "workspace_strategy_executive_e2e",
    scope_type: str = "COMPANY",
) -> dict[str, Any]:
    return {
        "target_id": target_id,
        "version": 1,
        "code": target_id.upper().replace(".", "-"),
        "name": target_id,
        "description": "Deterministic persistent strategy test target",
        "plan_ref": {"id": plan_id, "version": 1},
        "objective_ref": (None if objective_id is None else {"id": objective_id, "version": 1}),
        "metric_code": "KPI-E2E-COUNT",
        "scope": {
            "type": scope_type,
            "ref": None if scope_type == "COMPANY" else workspace_id,
        },
        "period": PERIOD,
        "measurement_type": "CUMULATIVE",
        "unit": "COUNT",
        "owner_workspace_id": workspace_id,
        "owner_role_ref": "EXECUTIVE" if scope_type == "COMPANY" else "WORKSPACE_LEAD",
        "materiality": "MATERIAL",
        "source_refs": ["source:approved-rkap"],
        "evidence_refs": ["evidence:approved-rkap"],
    }


def _observation_payload(
    observation_id: str,
    target_id: str,
    *,
    verified: bool = True,
) -> dict[str, Any]:
    return {
        "observation_id": observation_id,
        "target_id": target_id,
        "target_version": 1,
        "kind": "TARGET",
        "value": 7,
        "unit": "COUNT",
        "period": PERIOD,
        "source_ref": "evidence:approved-rkap",
        "source_mode": "MANUAL_EVIDENCED",
        "observed_at": "2026-09-27T00:00:00Z",
        "verified_at": "2026-09-27T00:01:00Z" if verified else None,
        "verification_state": "VERIFIED" if verified else "UNVERIFIED",
        "evidence_refs": ["evidence:approved-rkap"],
    }


async def _create_plan_target(
    context: StrategyContext,
    plan_id: str,
    target_id: str,
    *,
    evidence: bool = True,
    verified: bool = True,
) -> None:
    client = context.client
    headers = context.executive_headers
    response = await client.post(
        "/api/v1/strategy/plans", headers=headers, json=_plan_payload(plan_id, evidence=evidence)
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        "/api/v1/strategy/targets",
        headers=headers,
        json=_target_payload(target_id, plan_id),
    )
    assert response.status_code == 201, response.text
    response = await client.post(
        f"/api/v1/strategy/targets/{target_id}/observations",
        headers=headers,
        json=_observation_payload(f"observation.{target_id}", target_id, verified=verified),
    )
    assert response.status_code == 201, response.text


async def test_persistent_strategy_api_vertical_slice_and_scope_security(
    strategy_context: StrategyContext,
) -> None:
    context = strategy_context
    client = context.client
    headers = context.executive_headers
    plan_id = "plan.rkap.2027.e2e"
    objective_id = "objective.growth.e2e"
    root_target_id = "target.company.akad.e2e"
    division_target_id = "target.sales.leads.e2e"

    created_plan = await client.post(
        "/api/v1/strategy/plans", headers=headers, json=_plan_payload(plan_id)
    )
    assert created_plan.status_code == 201, created_plan.text
    assert created_plan.json()["lifecycle_state"] == "DRAFT"

    objective = await client.post(
        "/api/v1/strategy/objectives",
        headers=headers,
        json={
            "objective_id": objective_id,
            "version": 1,
            "plan_id": plan_id,
            "plan_version": 1,
            "code": "OBJ-E2E-01",
            "name": "Pertumbuhan terverifikasi",
            "scope": {"type": "COMPANY", "ref": None},
            "owner_role_ref": "EXECUTIVE",
        },
    )
    assert objective.status_code == 201, objective.text

    target = await client.post(
        "/api/v1/strategy/targets",
        headers=headers,
        json=_target_payload(root_target_id, plan_id, objective_id=objective_id),
    )
    assert target.status_code == 201, target.text
    observation = await client.post(
        f"/api/v1/strategy/targets/{root_target_id}/observations",
        headers=headers,
        json=_observation_payload("observation.company.akad.e2e", root_target_id),
    )
    assert observation.status_code == 201, observation.text
    assert observation.json()["value"] == "7"
    assert observation.json()["verification_state"] == "VERIFIED"

    assumption = await client.post(
        "/api/v1/strategy/assumptions",
        headers=headers,
        json={
            "assumption_id": "assumption.conversion.e2e",
            "version": 1,
            "category": "CONVERSION_RATIO",
            "name": "Verified lead-to-akad ratio",
            "value": 0.3,
            "unit": "RATIO",
            "period": PERIOD,
            "scope": {"type": "COMPANY", "ref": None},
            "source_ref": "evidence:conversion-study",
            "source_mode": "MANUAL_EVIDENCED",
            "evidence_refs": ["evidence:conversion-study"],
            "verification_state": "VERIFIED",
            "owner_role_ref": "EXECUTIVE",
            "owner_workspace_id": "workspace_strategy_executive_e2e",
        },
    )
    assert assumption.status_code == 201, assumption.text
    assert assumption.json()["value"] == "0.3"

    before_preview = await client.get("/api/v1/strategy/targets", headers=headers)
    assert before_preview.status_code == 200
    preview = await client.post(
        "/api/v1/strategy/cascade/preview",
        headers=headers,
        json={
            "root_target_ref": {"target_id": root_target_id, "version": 1},
            "rules": [
                {
                    "cascade_rule_id": "rule.required-leads.e2e",
                    "rule_type": "RATIO_DIVIDE_CEIL",
                    "input_target_refs": [{"target_id": root_target_id, "version": 1}],
                    "output_target_refs": [{"target_id": division_target_id, "version": 1}],
                    "parameters": {},
                }
            ],
            "rule_inputs": {"rule.required-leads.e2e": {"input": 7, "ratio": 0.3}},
            "assumption_refs": ["assumption.conversion.e2e"],
            "constraints": [
                {
                    "constraint_id": "constraint.capacity.e2e",
                    "constraint_type": "CAPACITY",
                    "critical": True,
                    "required_value": 24,
                    "available_value": 24,
                    "applies": True,
                }
            ],
        },
    )
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["status"] == "VALID"
    assert preview_body["assumptions_used"][0]["assumption_id"] == ("assumption.conversion.e2e")
    assert preview_body["calculation_trace"] == [
        {
            "rule_id": "rule.required-leads.e2e",
            "rule_type": "RATIO_DIVIDE_CEIL",
            "output_target_id": division_target_id,
            "inputs": {"input": "7", "ratio": "0.3"},
            "rounding_mode": "CEILING",
            "output": "24",
            "status": "VALID",
            "message": None,
        }
    ]
    assert preview_body["constraint_results"][0]["result"] == "PASS"
    after_preview = await client.get("/api/v1/strategy/targets", headers=headers)
    assert after_preview.status_code == 200
    assert [item["target_id"] for item in after_preview.json()] == [root_target_id]

    unauthorized_accept = await client.post(
        f"/api/v1/strategy/cascade-runs/{preview_body['cascade_run_id']}/accept",
        headers=context.unrelated_headers,
        json={"derived_targets": [_target_payload(division_target_id, plan_id)]},
    )
    assert unauthorized_accept.status_code == 403

    accepted = await client.post(
        f"/api/v1/strategy/cascade-runs/{preview_body['cascade_run_id']}/accept",
        headers=headers,
        json={
            "derived_targets": [
                _target_payload(
                    division_target_id,
                    plan_id,
                    objective_id=objective_id,
                    workspace_id="workspace_strategy_sales_e2e",
                    scope_type="DIVISION",
                )
            ]
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "ACCEPTED"
    persisted_derived = await client.get(
        f"/api/v1/strategy/targets/{division_target_id}", headers=headers
    )
    assert persisted_derived.status_code == 200
    assert persisted_derived.json()["target"]["lifecycle_state"] == "DRAFT"
    assert persisted_derived.json()["target"]["cascade_run_id"] == preview_body["cascade_run_id"]

    relationship_payload = {
        "relationship_id": "relationship.company-sales.e2e",
        "relationship_type": "CASCADE",
        "parent_target_id": root_target_id,
        "parent_target_version": 1,
        "child_target_id": division_target_id,
        "child_target_version": 1,
    }
    relationship = await client.post(
        f"/api/v1/strategy/targets/{root_target_id}/relationships",
        headers=headers,
        json=relationship_payload,
    )
    assert relationship.status_code == 201, relationship.text
    duplicate = await client.post(
        f"/api/v1/strategy/targets/{root_target_id}/relationships",
        headers=headers,
        json={**relationship_payload, "relationship_id": "relationship.duplicate.e2e"},
    )
    assert duplicate.status_code == 409
    self_link = await client.post(
        f"/api/v1/strategy/targets/{root_target_id}/relationships",
        headers=headers,
        json={
            **relationship_payload,
            "relationship_id": "relationship.self.e2e",
            "child_target_id": root_target_id,
        },
    )
    assert self_link.status_code == 409
    cycle = await client.post(
        f"/api/v1/strategy/targets/{division_target_id}/relationships",
        headers=headers,
        json={
            "relationship_id": "relationship.cycle.e2e",
            "relationship_type": "CASCADE",
            "parent_target_id": division_target_id,
            "parent_target_version": 1,
            "child_target_id": root_target_id,
            "child_target_version": 1,
        },
    )
    assert cycle.status_code == 409

    for foreign_headers in (context.cross_tenant_headers, context.cross_org_headers):
        denied = await client.get(f"/api/v1/strategy/plans/{plan_id}", headers=foreign_headers)
        assert denied.status_code == 403
    denied_permission = await client.get(
        "/api/v1/strategy/authority", headers=context.missing_permission_headers
    )
    assert denied_permission.status_code == 403
    denied_workspace = await client.post(
        "/api/v1/strategy/plans",
        headers=context.unrelated_headers,
        json={
            **_plan_payload("plan.cross-workspace.e2e"),
            "scope": {"type": "DIVISION", "ref": "workspace_strategy_sales_e2e"},
            "owner_workspace_id": "workspace_strategy_sales_e2e",
            "owner_role_ref": "WORKSPACE_LEAD",
        },
    )
    assert denied_workspace.status_code == 403

    assert (await client.post(f"/api/v1/strategy/plans/{plan_id}/submit", headers=headers)).json()[
        "lifecycle_state"
    ] == "UNDER_REVIEW"
    assert (await client.post(f"/api/v1/strategy/plans/{plan_id}/approve", headers=headers)).json()[
        "lifecycle_state"
    ] == "APPROVED"
    immutable = await client.patch(
        f"/api/v1/strategy/plans/{plan_id}", headers=headers, json={"name": "forbidden"}
    )
    assert immutable.status_code == 409
    activated = await client.post(f"/api/v1/strategy/plans/{plan_id}/activate", headers=headers)
    assert activated.status_code == 200, activated.text
    assert activated.json()["lifecycle_state"] == "ACTIVE"
    active_readback = await client.get(f"/api/v1/strategy/plans/{plan_id}", headers=headers)
    assert active_readback.json()["lifecycle_state"] == "ACTIVE"

    division_view = await client.get(
        f"/api/v1/strategy/targets/{division_target_id}", headers=context.sales_headers
    )
    assert division_view.status_code == 200
    assert division_view.json()["target"]["lifecycle_state"] == "ACTIVE"
    unrelated_view = await client.get(
        f"/api/v1/strategy/targets/{division_target_id}", headers=context.unrelated_headers
    )
    assert unrelated_view.status_code == 403

    revision = await client.post(
        f"/api/v1/strategy/targets/{root_target_id}/revisions",
        headers=headers,
        json={"reason": "Updated approved planning basis"},
    )
    assert revision.status_code == 201, revision.text
    assert revision.json()["target"]["version"] == 2
    assert revision.json()["target"]["lifecycle_state"] == "DRAFT"
    revisions = await client.get(
        f"/api/v1/strategy/targets/{root_target_id}/revisions", headers=headers
    )
    assert revisions.status_code == 200
    assert revisions.json()[0]["from_version"] == 1
    historical = await context.app.state.strategy_repository.get_target(root_target_id, 1)
    assert historical is not None
    assert historical.version == 1
    assert historical.lifecycle_state.value == "ACTIVE"

    async with context.app.state.database.session_factory() as session:
        audit_events = (
            await session.scalars(
                select(AuditRecord.event_type).where(AuditRecord.tenant_id == "tenant_strategy_e2e")
            )
        ).all()
    assert set(audit_events) >= {
        "PLAN_CREATED",
        "TARGET_CREATED",
        "TARGET_OBSERVATION_CREATED",
        "ASSUMPTION_CREATED",
        "ASSUMPTION_VERIFIED",
        "CASCADE_PREVIEWED",
        "CONSTRAINT_EVALUATED",
        "CASCADE_ACCEPTED",
        "RELATIONSHIP_CREATED",
        "PLAN_SUBMITTED",
        "PLAN_APPROVED",
        "PLAN_ACTIVATED",
        "TARGET_REVISION_CREATED",
    }

    async with context.app.state.database.session_factory() as session:
        persisted = (
            await session.scalars(
                select(StrategyTargetRecord).where(
                    StrategyTargetRecord.target_id == division_target_id
                )
            )
        ).all()
    assert len(persisted) == 1
    assert persisted[0].payload["lifecycle_state"] == "ACTIVE"


async def test_persistent_strategy_failure_states_block_closed(
    strategy_context: StrategyContext,
) -> None:
    context = strategy_context
    client = context.client
    headers = context.executive_headers

    await _create_plan_target(context, "plan.invalid-cascade.e2e", "target.invalid-cascade.e2e")
    missing_assumption = await client.post(
        "/api/v1/strategy/cascade/preview",
        headers=headers,
        json={
            "root_target_ref": {"target_id": "target.invalid-cascade.e2e", "version": 1},
            "rules": [
                {
                    "cascade_rule_id": "rule.missing-ratio.e2e",
                    "rule_type": "RATIO_DIVIDE_CEIL",
                    "output_target_refs": [{"target_id": "target.incomplete.e2e", "version": 1}],
                    "parameters": {},
                }
            ],
            "rule_inputs": {"rule.missing-ratio.e2e": {"input": 7, "ratio": 0.3}},
            "assumption_refs": ["assumption.missing.e2e"],
            "constraints": [],
        },
    )
    assert missing_assumption.status_code == 200
    assert missing_assumption.json()["status"] == "INCOMPLETE"
    assert "assumption.missing.e2e" in missing_assumption.json()["blocking_conditions"][0]
    zero_ratio = await client.post(
        "/api/v1/strategy/cascade/preview",
        headers=headers,
        json={
            "root_target_ref": {"target_id": "target.invalid-cascade.e2e", "version": 1},
            "rules": [
                {
                    "cascade_rule_id": "rule.zero-ratio.e2e",
                    "rule_type": "RATIO_DIVIDE_CEIL",
                    "output_target_refs": [{"target_id": "target.invalid.e2e", "version": 1}],
                    "parameters": {},
                }
            ],
            "rule_inputs": {"rule.zero-ratio.e2e": {"input": 7, "ratio": 0}},
            "assumption_refs": [],
            "constraints": [],
        },
    )
    assert zero_ratio.status_code == 200
    assert zero_ratio.json()["status"] == "INVALID"

    await _create_plan_target(
        context, "plan.unverified.e2e", "target.unverified.e2e", verified=False
    )
    await client.post("/api/v1/strategy/plans/plan.unverified.e2e/submit", headers=headers)
    await client.post("/api/v1/strategy/plans/plan.unverified.e2e/approve", headers=headers)
    unverified = await client.post(
        "/api/v1/strategy/plans/plan.unverified.e2e/activate", headers=headers
    )
    assert unverified.status_code == 409
    assert unverified.json()["code"] == "STRATEGY_OBSERVATION_BLOCKED"

    await _create_plan_target(
        context, "plan.missing-evidence.e2e", "target.missing-evidence.e2e", evidence=False
    )
    await client.post("/api/v1/strategy/plans/plan.missing-evidence.e2e/submit", headers=headers)
    await client.post("/api/v1/strategy/plans/plan.missing-evidence.e2e/approve", headers=headers)
    missing_evidence = await client.post(
        "/api/v1/strategy/plans/plan.missing-evidence.e2e/activate", headers=headers
    )
    assert missing_evidence.status_code == 409
    assert missing_evidence.json()["code"] == "STRATEGY_EVIDENCE_REQUIRED"

    for suffix, constraint in (
        ("fail", Constraint("constraint.fail.e2e", "CAPACITY", True, Decimal("10"), Decimal("9"))),
        ("unknown", Constraint("constraint.unknown.e2e", "CAPACITY", True, Decimal("10"), None)),
    ):
        plan_id = f"plan.constraint-{suffix}.e2e"
        created = await client.post(
            "/api/v1/strategy/plans", headers=headers, json=_plan_payload(plan_id)
        )
        assert created.status_code == 201
        await client.post(f"/api/v1/strategy/plans/{plan_id}/submit", headers=headers)
        await client.post(f"/api/v1/strategy/plans/{plan_id}/approve", headers=headers)
        with pytest.raises(Exception) as blocked:
            await context.app.state.strategy_service.activate_plan(
                context.executive, plan_id, (constraint,)
            )
        assert getattr(blocked.value, "code", None) == "STRATEGY_CONSTRAINT_BLOCKED"
