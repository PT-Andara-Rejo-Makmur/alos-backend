from alos.audit import InMemoryAuditRepository
from alos.authorization import AuthorizationEnforcer, AuthorizationOutcome, AuthorizationPolicy
from alos.identity import Principal
from alos.permissions import PermissionRegistry


async def test_permission_only_command_allows_principal_without_scopes() -> None:
    enforcer = AuthorizationEnforcer(
        policy=AuthorizationPolicy(),
        permissions=PermissionRegistry(),
        audit=InMemoryAuditRepository(),
    )
    principal = Principal(
        actor_id="actor_it_admin",
        tenant_id="tenant_default",
        organization_id="org_default",
        workspace_id="workspace_it",
        permissions=frozenset({"identity.accounts.manage"}),
        scopes=frozenset(),
        roles=frozenset({"IT_ADMIN"}),
    )

    decision = await enforcer.enforce(
        principal=principal,
        required_permission="identity.accounts.manage",
        correlation_id="corr_permission_only",
        command="identity.account.provision",
    )

    assert decision.outcome is AuthorizationOutcome.ALLOWED
