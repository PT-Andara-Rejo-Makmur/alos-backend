"""Executive authority remains membership-bound and explicitly permission-gated."""

from dataclasses import replace
from datetime import UTC, datetime

import httpx
import pytest

from alos.api.public.strategy_routes import _plan_projection
from alos.authentication.memory import InMemoryAuthRepository
from alos.authentication.service import AuthService, _resolve_default_permissions
from alos.config import Settings
from alos.dependencies import get_current_principal
from alos.domains.strategy.authority import authorize
from alos.domains.strategy.models import LifecycleState, Period, Plan, PlanType, ScopeRef
from alos.identity import Principal
from alos.main import create_app
from alos.security.errors import PlatformError

EXPECTED = frozenset(
    {
        "navigation.read",
        "work.read",
        "strategy.read",
        "strategy.company.manage",
        "strategy.review",
        "strategy.approve",
        "strategy.activate",
    }
)


@pytest.mark.parametrize("division", [None, "FINANCE", "SALES", "PROPERTY", "LEGAL", "HR", "IT"])
def test_executive_defaults_do_not_grant_identity_or_business_write(division):
    assert (
        frozenset(_resolve_default_permissions(division, "EXECUTIVE", ("EXECUTIVE",))) == EXPECTED
    )
    assert "strategy.company.manage" not in _resolve_default_permissions(
        "IT", "IT_OPERATIONS", ("IT_ADMIN",)
    )


@pytest.mark.asyncio
async def test_canonical_provisioning_resolves_executive_company_authority():
    repository = InMemoryAuthRepository()
    service = AuthService(repository)
    await service.bootstrap_initial_admin(
        {
            "email": "admin@example.test",
            "password": "StrongPass!123",
            "display_name": "Administrator",
            "tenant_id": "tenant_01",
            "organization_id": "org_01",
            "workspace_id": "workspace_01",
            "workspace_key": "executive",
            "workspace_name": "Company",
            "workspace_type": "EXECUTIVE",
        }
    )
    repository.add_test_employee(
        "employee_01",
        tenant_id="tenant_01",
        organization_id="org_01",
        full_name="Executive",
        email="executive@example.test",
        join_date=datetime.now(UTC).date(),
    )
    account = await service.provision(
        {
            "employee_id": "employee_01",
            "tenant_id": "tenant_01",
            "organization_id": "org_01",
            "workspace_id": "workspace_01",
            "role_refs": ["EXECUTIVE"],
        }
    )
    access = account["workspace_access"][0]
    assert frozenset(access["permission_refs"]) == EXPECTED
    assert access["role_refs"] == ["EXECUTIVE"]


@pytest.mark.parametrize("action", ["company_manage", "review", "approve", "activate"])
@pytest.mark.parametrize("role", ["IT_ADMIN", "DIVISION_LEAD", "DIVISION_MEMBER"])
def test_company_actions_require_executive_role_even_with_permissions(action, role):
    actor = Principal(
        "actor_01",
        "tenant_01",
        "org_01",
        "workspace_01",
        permissions=EXPECTED,
        roles=frozenset({role}),
    )
    with pytest.raises(PlatformError, match="Required strategy role"):
        authorize(actor, action, tenant_id="tenant_01", organization_id="org_01")


@pytest.mark.parametrize("action", ["read", "company_manage", "review", "approve", "activate"])
def test_executive_needs_permission_and_active_matching_boundary(action):
    actor = Principal(
        "actor_01",
        "tenant_01",
        "org_01",
        "workspace_01",
        permissions=EXPECTED,
        roles=frozenset({"EXECUTIVE"}),
    )
    authorize(actor, action, tenant_id="tenant_01", organization_id="org_01")
    for denied in (
        replace(actor, permissions=frozenset()),
        replace(actor, active=False),
        replace(actor, tenant_id="tenant_other"),
        replace(actor, organization_id="org_other"),
    ):
        with pytest.raises(PlatformError):
            authorize(denied, action, tenant_id="tenant_01", organization_id="org_01")


def test_division_workspace_mutation_boundary_remains_enforced():
    actor = Principal(
        "actor_01",
        "tenant_01",
        "org_01",
        "workspace_01",
        permissions=frozenset({"strategy.division.manage"}),
        roles=frozenset({"DIVISION_LEAD"}),
    )
    with pytest.raises(PlatformError) as failure:
        authorize(
            actor,
            "division_manage",
            tenant_id="tenant_01",
            organization_id="org_01",
            owner_workspace_id="workspace_other",
        )
    assert failure.value.code == "STRATEGY_WORKSPACE_DENIED"


@pytest.mark.asyncio
async def test_executive_cannot_administer_identity_or_write_business_domains():
    app = create_app(Settings(_env_file=None, APP_ENV="test"))
    actor = Principal(
        "actor_01",
        "tenant_01",
        "org_01",
        "workspace_01",
        permissions=EXPECTED,
        roles=frozenset({"EXECUTIVE"}),
    )
    app.dependency_overrides[get_current_principal] = lambda: actor
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ("/api/v1/identity/accounts", "/api/v1/identity/assignable-roles"):
            response = await client.get(path)
            assert response.status_code == 403, response.text

        for domain, resource in (
            ("sales", "leads"),
            ("finance", "bank_transactions"),
            ("property", "property_units"),
            ("legal", "contracts"),
            ("hr", "employees"),
            ("it", "incidents"),
        ):
            response = await client.post(f"/api/v1/domains/{domain}/{resource}", json={})
            assert response.status_code == 403, response.text


@pytest.mark.asyncio
async def test_executive_permissions_follow_active_membership_and_role_changes():
    service = AuthService(InMemoryAuthRepository())
    account = await service.register_for_test(
        {
            "email": "executive@example.test",
            "password": "StrongPass!123",
            "tenant_id": "tenant_01",
            "organization_id": "org_01",
            "workspace_id": "workspace_exec",
            "workspace_key": "executive",
            "workspace_type": "EXECUTIVE",
            "role_refs": ["EXECUTIVE"],
        }
    )
    await service.register_for_test(
        {
            "email": "member@example.test",
            "password": "StrongPass!123",
            "tenant_id": "tenant_01",
            "organization_id": "org_01",
            "workspace_id": "workspace_sales",
            "workspace_key": "sales",
            "workspace_type": "BUSINESS",
            "division_code": "SALES",
            "role_refs": ["DIVISION_MEMBER"],
        }
    )
    await service.assign_membership(
        account["actor_id"],
        {"workspace_id": "workspace_sales", "role_refs": ["DIVISION_MEMBER"]},
        tenant_id="tenant_01",
        organization_id="org_01",
    )
    session = await service.login("executive@example.test", "StrongPass!123")
    token = session["access_token"]
    selected = await service.select_active_workspace(token, "workspace_exec")
    assert frozenset(selected["membership"]["permission_refs"]) == EXPECTED
    switched = await service.select_active_workspace(token, "workspace_sales")
    assert "strategy.company.manage" not in switched["membership"]["permission_refs"]
    assert switched["membership"]["role_refs"] == ["DIVISION_MEMBER"]
    restored = await service.select_active_workspace(token, "workspace_exec")
    assert frozenset(restored["membership"]["permission_refs"]) == EXPECTED
    await service.update_membership(
        account["actor_id"],
        {"workspace_id": "workspace_exec", "role_refs": ["DIVISION_MEMBER"]},
        tenant_id="tenant_01",
        organization_id="org_01",
    )
    fresh = await service.whoami(token)
    assert "strategy.company.manage" not in fresh["active_workspace"]["permission_refs"]


@pytest.mark.parametrize(
    ("role", "permission", "scope", "actions"),
    [
        ("EXECUTIVE", "strategy.company.manage", "COMPANY", ["EDIT", "SUBMIT"]),
        ("EXECUTIVE", "strategy.company.manage", "DIVISION", []),
        ("DIVISION_LEAD", "strategy.division.manage", "COMPANY", []),
        ("DIVISION_LEAD", "strategy.division.manage", "DIVISION", ["EDIT", "SUBMIT"]),
    ],
)
def test_plan_action_projection_matches_company_and_division_mutation_scope(
    role, permission, scope, actions
):
    actor = Principal(
        "actor_01",
        "tenant_01",
        "org_01",
        "workspace_01",
        permissions=frozenset({permission}),
        roles=frozenset({role}),
    )
    plan = Plan(
        "plan_01",
        1,
        PlanType.OPERATING_PLAN,
        "Planning evidence",
        "tenant_01",
        "org_01",
        "workspace_01",
        role,
        Period("ANNUAL", "2027-01-01", "2027-12-31"),
        ScopeRef(scope),
        LifecycleState.DRAFT,
        "actor_01",
        "corr_01",
    )
    assert _plan_projection(plan, actor)["authorized_actions"] == actions
