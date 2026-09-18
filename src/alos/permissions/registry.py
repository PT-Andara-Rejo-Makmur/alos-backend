"""Backend-owned RBAC registry; missing or inactive grants deny access."""

from dataclasses import dataclass, field


class PermissionConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RoleGrant:
    role_id: str
    tenant_id: str
    organization_id: str
    permission_refs: frozenset[str] = field(default_factory=frozenset)
    scope_refs: frozenset[str] = field(default_factory=frozenset)
    active: bool = True


class PermissionRegistry:
    def __init__(self) -> None:
        self._grants: dict[tuple[str, str, str], RoleGrant] = {}

    def register(self, grant: RoleGrant) -> None:
        key = (grant.tenant_id, grant.organization_id, grant.role_id)
        if key in self._grants:
            raise PermissionConflictError("role grant already exists")
        self._grants[key] = grant

    def resolve(
        self,
        *,
        tenant_id: str,
        organization_id: str,
        roles: frozenset[str],
    ) -> tuple[frozenset[str], frozenset[str]]:
        permissions: set[str] = set()
        scopes: set[str] = set()
        for role in roles:
            grant = self._grants.get((tenant_id, organization_id, role))
            if grant is None or not grant.active:
                continue
            permissions.update(grant.permission_refs)
            scopes.update(grant.scope_refs)
        return frozenset(permissions), frozenset(scopes)
