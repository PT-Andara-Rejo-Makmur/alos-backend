import pytest

from alos.authentication.memory import InMemoryAuthRepository
from alos.authentication.service import AuthService, _resolve_default_permissions
from alos.cli import build_parser
from alos.security.errors import PlatformError


def _payload(email: str = "initial-admin@andara.local") -> dict[str, object]:
    return {
        "email": email,
        "password": "StrongPass!123",
        "display_name": "Initial Administrator",
        "tenant_id": "tenant_operator_supplied",
        "organization_id": "org_operator_supplied",
        "workspace_id": "workspace_operator_supplied",
        "workspace_key": "identity-admin",
        "workspace_name": "Identity Administration",
        "workspace_type": "IT_OPERATIONS",
        "role_refs": ["EXECUTIVE"],
        "permission_refs": ["arbitrary.permission"],
    }


@pytest.mark.asyncio
async def test_initial_bootstrap_creates_minimal_canonical_authority_once() -> None:
    service = AuthService(InMemoryAuthRepository())

    result = await service.bootstrap_initial_admin(_payload())

    access = result["workspace_access"][0]
    assert access["role_refs"] == ["IT_ADMIN"]
    assert access["permission_refs"] == [
        "identity.accounts.manage",
        "identity.memberships.manage",
        "identity.memberships.read",
    ]
    assert access["scope_refs"] == ["scope.identity.manage"]
    assert access["data_scope"] == "COMPANY"

    with pytest.raises(PlatformError) as raised:
        await service.bootstrap_initial_admin(_payload("second-admin@andara.local"))
    assert raised.value.code == "IDENTITY_BOOTSTRAP_EXISTS"
    assert raised.value.status_code == 409


def test_bootstrap_cli_has_no_password_or_override_argument() -> None:
    options = {
        action.dest
        for action in build_parser()
        ._subparsers._group_actions[0]
        .choices[  # type: ignore[union-attr]
            "bootstrap-identity"
        ]
        ._actions
    }
    assert "password" not in options
    assert "override" not in options


def test_workspace_metadata_limits_default_permissions() -> None:
    it_admin = _resolve_default_permissions("IT", "IT_OPERATIONS", ("IT_ADMIN",))
    assert "identity.accounts.manage" in it_admin
    assert "finance.read" not in it_admin

    finance_member = _resolve_default_permissions("FINANCE", "BUSINESS", ("DIVISION_MEMBER",))
    assert "finance.read" in finance_member
    assert "it.read" not in finance_member
    assert "identity.accounts.manage" not in finance_member


@pytest.mark.asyncio
async def test_provision_uses_backend_workspace_division_for_arbitrary_key() -> None:
    repository = InMemoryAuthRepository()
    service = AuthService(repository)
    await service.register_for_test(
        {
            "email": "workspace-owner@andara.local",
            "password": "StrongPass!123",
            "tenant_id": "tenant_01",
            "organization_id": "org_01",
            "workspace_id": "workspace_finance",
            "workspace_key": "arbitrary-key",
            "workspace_name": "Finance",
            "workspace_type": "BUSINESS",
            "division_code": "FINANCE",
            "role_refs": ["DIVISION_LEAD"],
        }
    )
    repository.add_test_employee(
        "employee_01", tenant_id="tenant_01", organization_id="org_01", full_name="Employee"
    )
    account = await service.provision(
        {
            "email": "employee@andara.local",
            "employee_id": "employee_01",
            "tenant_id": "tenant_01",
            "organization_id": "org_01",
            "workspace_id": "workspace_finance",
            "workspace_key": "it",
            "division_code": "IT",
            "role_refs": ["DIVISION_MEMBER"],
        }
    )
    permissions = account["workspace_access"][0]["permission_refs"]
    assert "finance.read" in permissions
    assert "it.read" not in permissions
