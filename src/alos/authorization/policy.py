"""Tenant, workspace, permission, and scope enforcement."""

from collections.abc import Collection

from alos.identity import Principal


class AuthorizationPolicy:
    """An explicit allow policy. Any missing fact produces a denial."""

    def is_allowed(
        self,
        principal: Principal | None,
        *,
        tenant_id: str,
        workspace_id: str,
        required_permission: str,
        required_scopes: Collection[str] = (),
    ) -> bool:
        if principal is None:
            return False
        if principal.tenant_id != tenant_id or principal.workspace_id != workspace_id:
            return False
        if required_permission not in principal.permissions:
            return False
        return set(required_scopes).issubset(principal.scopes)
