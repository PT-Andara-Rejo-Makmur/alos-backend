import pytest

from alos.api.public.release_routes import _authorize
from alos.identity import Principal
from alos.security.errors import PlatformError


def principal() -> Principal:
    return Principal(
        actor_id="actor_it",
        tenant_id="tenant_release",
        organization_id="org_release",
        workspace_id="workspace_release",
        permissions=frozenset({"release.manage"}),
        scopes=frozenset({"scope.release"}),
        roles=frozenset({"IT_ADMIN"}),
    )


def test_release_api_rejects_forged_actor_and_context() -> None:
    for field, forged in (
        ("actor_id", "actor_attacker"),
        ("tenant_id", "tenant_other"),
        ("organization_id", "org_other"),
        ("workspace_id", "workspace_other"),
    ):
        with pytest.raises(PlatformError) as captured:
            _authorize(
                principal(),
                {field: forged},
                permission="release.manage",
                role="IT_ADMIN",
            )
        assert captured.value.code == "RELEASE_AUTHORITY_MISMATCH"


@pytest.mark.parametrize("missing", ("permission", "role"))
def test_release_api_requires_backend_permission_and_role(missing: str) -> None:
    current = principal()
    if missing == "permission":
        current = Principal(
            actor_id=current.actor_id,
            tenant_id=current.tenant_id,
            organization_id=current.organization_id,
            workspace_id=current.workspace_id,
            roles=current.roles,
            scopes=current.scopes,
        )
    else:
        current = Principal(
            actor_id=current.actor_id,
            tenant_id=current.tenant_id,
            organization_id=current.organization_id,
            workspace_id=current.workspace_id,
            permissions=current.permissions,
            scopes=current.scopes,
        )
    with pytest.raises(PlatformError) as captured:
        _authorize(current, {}, permission="release.manage", role="IT_ADMIN")
    assert captured.value.code == "RELEASE_NOT_AUTHORIZED"
