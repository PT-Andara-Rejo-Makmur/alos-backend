from alos.identity import Actor, Membership, Organization, Tenant, Workspace
from alos.identity.directory import IdentityConflictError, IdentityDirectory
from alos.permissions import PermissionRegistry, RoleGrant


def test_identity_directory_builds_tenant_scoped_principal() -> None:
    directory = IdentityDirectory()
    directory.add_tenant(Tenant(tenant_id="tenant_001", name="Andara"))
    directory.add_organization(
        Organization(
            organization_id="org_001",
            tenant_id="tenant_001",
            name="Andara Rejo Makmur",
        )
    )
    directory.add_workspace(
        Workspace(
            workspace_id="workspace_001",
            tenant_id="tenant_001",
            organization_id="org_001",
            name="Operations",
        )
    )
    directory.add_actor(
        Actor(
            actor_id="actor_001",
            tenant_id="tenant_001",
            organization_id="org_001",
            display_name="Operations Lead",
        )
    )
    directory.add_membership(
        Membership(
            actor_id="actor_001",
            tenant_id="tenant_001",
            organization_id="org_001",
            workspace_id="workspace_001",
            roles=frozenset({"DIVISION_LEAD"}),
            permissions=frozenset({"documents.read"}),
            scopes=frozenset({"scope.workspace.001"}),
        )
    )

    principal = directory.principal_for("actor_001", "workspace_001")

    assert principal is not None
    assert principal.tenant_id == "tenant_001"
    assert principal.permissions == frozenset({"documents.read"})


def test_identity_directory_rejects_cross_tenant_workspace() -> None:
    directory = IdentityDirectory()
    directory.add_tenant(Tenant(tenant_id="tenant_a", name="A"))
    directory.add_tenant(Tenant(tenant_id="tenant_b", name="B"))
    directory.add_organization(
        Organization(organization_id="org_a", tenant_id="tenant_a", name="A")
    )

    try:
        directory.add_workspace(
            Workspace(
                workspace_id="workspace_bad",
                tenant_id="tenant_b",
                organization_id="org_a",
                name="Invalid",
            )
        )
    except IdentityConflictError as exc:
        assert "boundary" in str(exc)
    else:
        raise AssertionError("cross-tenant workspace must be rejected")


def test_identity_directory_rejects_membership_for_unknown_actor() -> None:
    directory = IdentityDirectory()
    directory.add_tenant(Tenant(tenant_id="tenant_001", name="Andara"))
    directory.add_organization(
        Organization(organization_id="org_001", tenant_id="tenant_001", name="Andara")
    )
    directory.add_workspace(
        Workspace(
            workspace_id="workspace_001",
            tenant_id="tenant_001",
            organization_id="org_001",
            name="Operations",
        )
    )

    try:
        directory.add_membership(
            Membership(
                actor_id="actor_unknown",
                tenant_id="tenant_001",
                organization_id="org_001",
                workspace_id="workspace_001",
            )
        )
    except IdentityConflictError as exc:
        assert "boundary" in str(exc)
    else:
        raise AssertionError("membership for an unknown actor must be rejected")


def test_permission_registry_resolves_only_same_tenant_roles() -> None:
    registry = PermissionRegistry()
    registry.register(
        RoleGrant(
            role_id="IT_ADMIN",
            tenant_id="tenant_a",
            organization_id="org_a",
            permission_refs=frozenset({"agents.approve"}),
            scope_refs=frozenset({"scope.org.a"}),
        )
    )

    permissions, scopes = registry.resolve(
        tenant_id="tenant_b",
        organization_id="org_a",
        roles=frozenset({"IT_ADMIN"}),
    )

    assert not permissions
    assert not scopes
