"""Tenant, workspace, division, project, permission, and scope enforcement."""

from collections.abc import Collection

from alos.identity import Principal


class AuthorizationPolicy:
    """An explicit allow policy. Any missing fact produces a denial."""

    def is_allowed(
        self,
        principal: Principal | None,
        *,
        tenant_id: str,
        organization_id: str,
        workspace_id: str,
        required_permission: str,
        required_scopes: Collection[str] = (),
        division_id: str | None = None,
        project_id: str | None = None,
    ) -> bool:
        if principal is None:
            return False
        if not principal.active:
            return False
        if (
            principal.tenant_id != tenant_id
            or principal.organization_id != organization_id
            or principal.workspace_id != workspace_id
        ):
            return False
        # Division scope: if a command targets a division, the principal must match.
        if division_id is not None:
            if principal.division_id is None or principal.division_id != division_id:
                return False
        # Project scope: if a command targets a project, the principal must match.
        if project_id is not None:
            if principal.project_id is None or principal.project_id != project_id:
                return False
        if required_permission not in principal.permissions:
            return False
        return set(required_scopes).issubset(principal.scopes)

