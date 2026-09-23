import pytest

from alos.authentication.memory import InMemoryAuthRepository
from alos.authentication.service import AuthService
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
        for action in build_parser()._subparsers._group_actions[0].choices[  # type: ignore[union-attr]
            "bootstrap-identity"
        ]._actions
    }
    assert "password" not in options
    assert "override" not in options
