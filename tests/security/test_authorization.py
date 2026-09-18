from alos.authorization import AuthorizationPolicy
from alos.identity import Principal


def test_authorization_denies_missing_principal() -> None:
    policy = AuthorizationPolicy()
    assert not policy.is_allowed(
        None,
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        required_permission="tools.execute",
    )


def test_authorization_denies_cross_tenant_access() -> None:
    policy = AuthorizationPolicy()
    principal = Principal(
        actor_id="actor_001",
        tenant_id="tenant_a",
        organization_id="org_001",
        workspace_id="workspace_001",
        permissions=frozenset({"tools.execute"}),
    )
    assert not policy.is_allowed(
        principal,
        tenant_id="tenant_b",
        organization_id="org_001",
        workspace_id="workspace_001",
        required_permission="tools.execute",
    )
