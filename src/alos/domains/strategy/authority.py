"""Role, permission, tenant, organization, and workspace strategy authority."""

from alos.identity import Principal
from alos.security.errors import PlatformError

_ROLE_BY_ACTION = {
    "company_manage": frozenset({"EXECUTIVE"}),
    "division_manage": frozenset({"DIVISION_LEAD"}),
    "review": frozenset({"EXECUTIVE"}),
    "approve": frozenset({"EXECUTIVE"}),
    "activate": frozenset({"EXECUTIVE"}),
}
_PERMISSION_BY_ACTION = {
    "read": "strategy.read",
    "company_manage": "strategy.company.manage",
    "division_manage": "strategy.division.manage",
    "review": "strategy.review",
    "approve": "strategy.approve",
    "activate": "strategy.activate",
}


def authorize(
    principal: Principal,
    action: str,
    *,
    tenant_id: str,
    organization_id: str,
    owner_workspace_id: str | None = None,
) -> None:
    permission = _PERMISSION_BY_ACTION[action]
    if (
        not principal.active
        or principal.tenant_id != tenant_id
        or principal.organization_id != organization_id
    ):
        raise PlatformError(
            "STRATEGY_SCOPE_DENIED",
            "Strategy scope is outside authenticated authority.",
            status_code=403,
        )
    if permission not in principal.permissions:
        raise PlatformError(
            "STRATEGY_PERMISSION_DENIED", f"Permission {permission} is required.", status_code=403
        )
    roles = _ROLE_BY_ACTION.get(action)
    if roles is not None and not roles.intersection(principal.roles):
        raise PlatformError(
            "STRATEGY_ROLE_DENIED", "Required strategy role is missing.", status_code=403
        )
    if action == "division_manage" and owner_workspace_id != principal.workspace_id:
        raise PlatformError(
            "STRATEGY_WORKSPACE_DENIED",
            "Division mutation must remain in the active workspace.",
            status_code=403,
        )
