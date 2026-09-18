"""Tenant-aware identity directory with explicit membership boundaries."""

from typing import TypeVar

from alos.identity import Actor, Membership, Organization, Principal, Tenant, Workspace

KeyT = TypeVar("KeyT")
ValueT = TypeVar("ValueT")


class IdentityConflictError(ValueError):
    pass


class IdentityDirectory:
    """In-memory authority model used by services; persistence is injected separately."""

    def __init__(self) -> None:
        self._tenants: dict[str, Tenant] = {}
        self._organizations: dict[str, Organization] = {}
        self._workspaces: dict[str, Workspace] = {}
        self._actors: dict[str, Actor] = {}
        self._memberships: dict[tuple[str, str], Membership] = {}

    def add_tenant(self, tenant: Tenant) -> None:
        self._insert_unique(self._tenants, tenant.tenant_id, tenant)

    def add_organization(self, organization: Organization) -> None:
        tenant = self._tenants.get(organization.tenant_id)
        if tenant is None or not tenant.active:
            raise IdentityConflictError("organization requires an active tenant")
        self._insert_unique(self._organizations, organization.organization_id, organization)

    def add_workspace(self, workspace: Workspace) -> None:
        organization = self._organizations.get(workspace.organization_id)
        if (
            organization is None
            or not organization.active
            or organization.tenant_id != workspace.tenant_id
        ):
            raise IdentityConflictError("workspace tenant and organization boundary is invalid")
        self._insert_unique(self._workspaces, workspace.workspace_id, workspace)

    def add_actor(self, actor: Actor) -> None:
        organization = self._organizations.get(actor.organization_id)
        if (
            organization is None
            or not organization.active
            or organization.tenant_id != actor.tenant_id
        ):
            raise IdentityConflictError("actor tenant and organization boundary is invalid")
        self._insert_unique(self._actors, actor.actor_id, actor)

    def add_membership(self, membership: Membership) -> None:
        workspace = self._workspaces.get(membership.workspace_id)
        actor = self._actors.get(membership.actor_id)
        if (
            workspace is None
            or not workspace.active
            or actor is None
            or not actor.active
            or workspace.tenant_id != membership.tenant_id
            or workspace.organization_id != membership.organization_id
            or actor.tenant_id != membership.tenant_id
            or actor.organization_id != membership.organization_id
        ):
            raise IdentityConflictError("membership is outside the workspace authority boundary")
        self._insert_unique(
            self._memberships,
            (membership.actor_id, membership.workspace_id),
            membership,
        )

    def principal_for(self, actor_id: str, workspace_id: str) -> Principal | None:
        membership = self._memberships.get((actor_id, workspace_id))
        if membership is None or not membership.active:
            return None
        return Principal(
            actor_id=membership.actor_id,
            tenant_id=membership.tenant_id,
            organization_id=membership.organization_id,
            workspace_id=membership.workspace_id,
            permissions=membership.permissions,
            scopes=membership.scopes,
            roles=membership.roles,
            data_scope=membership.data_scope,
        )

    @staticmethod
    def _insert_unique(store: dict[KeyT, ValueT], key: KeyT, value: ValueT) -> None:
        if key in store:
            raise IdentityConflictError(f"identity record already exists: {key}")
        store[key] = value
