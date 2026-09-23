import httpx
import pytest


async def _register_identity(
    client: httpx.AsyncClient,
    *,
    email: str,
    tenant_id: str,
    organization_id: str,
    workspace_id: str,
    permissions: list[str] | None = None,
) -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass!123",
            "display_name": email.split("@", 1)[0],
            "tenant_id": tenant_id,
            "organization_id": organization_id,
            "workspace_id": workspace_id,
            "workspace_key": workspace_id,
            "workspace_name": workspace_id,
            "role_refs": ["IT_ADMIN"],
            "permission_refs": permissions or [],
            "scope_refs": ["scope.identity.manage"] if permissions else [],
        },
    )
    assert response.status_code == 201
    return response.json()


async def _login_headers(client: httpx.AsyncClient, email: str) -> dict[str, str]:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "StrongPass!123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _provision_payload(*, workspace_id: str, email: str) -> dict:
    return {
        "email": email,
        "password": "StrongPass!456",
        "display_name": "Provisioned Account",
        "workspace_id": workspace_id,
        "role_refs": ["WORKSPACE_MEMBER"],
    }


@pytest.mark.asyncio
async def test_register_and_login_round_trip(client: httpx.AsyncClient) -> None:
    register_response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "ops@andara.local",
            "password": "StrongPass!123",
            "display_name": "Operations Lead",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_operations",
            "roles": ["DIVISION_LEAD"],
            "permissions": ["documents.read", "documents.write"],
            "scopes": ["scope.workspace.operations", "scope.division.operations"],
            "data_scope": "PROJECT",
        },
    )

    assert register_response.status_code == 201
    payload = register_response.json()
    assert payload["email"] == "ops@andara.local"
    assert payload["workspace_access"][0]["workspace"]["workspace_id"] == "workspace_operations"
    assert payload["actor"]["actor_id"]

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "ops@andara.local", "password": "StrongPass!123"},
    )

    assert login_response.status_code == 200
    body = login_response.json()
    assert body["token_type"] == "bearer"  # noqa: S105 - OAuth2 token type constant, not a password
    active = body["principal"]["active_workspace"]
    assert active["workspace"]["workspace_id"] == "workspace_operations"
    assert "scope.workspace.operations" in active["scope_refs"]
    assert active["role_refs"] == ["WORKSPACE_LEAD"]


@pytest.mark.asyncio
async def test_whoami_returns_authenticated_principal(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "whoami@andara.local",
            "password": "StrongPass!123",
            "display_name": "Whoami User",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_operations",
            "roles": ["DIVISION_MEMBER"],
            "permissions": ["documents.read"],
            "scopes": ["scope.workspace.operations"],
            "data_scope": "PROJECT",
        },
    )
    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "whoami@andara.local", "password": "StrongPass!123"},
    )
    token = login_response.json()["access_token"]

    whoami_response = await client.get(
        "/api/v1/auth/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert whoami_response.status_code == 200
    whoami = whoami_response.json()
    assert whoami["email"] == "whoami@andara.local"
    assert whoami["active_workspace"]["workspace"]["workspace_id"] == "workspace_operations"


@pytest.mark.asyncio
async def test_workspace_listing_and_selection_fail_closed(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "workspace@andara.local",
            "password": "StrongPass!123",
            "display_name": "Workspace User",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_operations",
            "role_refs": ["WORKSPACE_MEMBER"],
            "permission_refs": ["documents.read"],
            "scope_refs": ["scope.workspace.operations"],
            "data_scope": "WORKSPACE",
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "workspace@andara.local", "password": "StrongPass!123"},
    )
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    listing = await client.get("/api/v1/workspaces", headers=headers)
    assert listing.status_code == 200
    assert [item["workspace"]["workspace_id"] for item in listing.json()] == [
        "workspace_operations"
    ]

    denied = await client.put(
        "/api/v1/auth/active-workspace",
        headers=headers,
        json={"workspace_id": "workspace_unauthorized"},
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == "WORKSPACE_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_logout_revokes_session(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "logout@andara.local",
            "password": "StrongPass!123",
            "display_name": "Logout User",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_operations",
            "role_refs": ["WORKSPACE_MEMBER"],
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "logout@andara.local", "password": "StrongPass!123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/auth/whoami", headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_unknown_credentials(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "missing@andara.local", "password": "wrongpassword"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_account_provisioning_uses_permission_policy_and_records_actor(
    client: httpx.AsyncClient,
) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "identity-admin@andara.local",
            "password": "StrongPass!123",
            "display_name": "Identity Admin",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_operations",
            "role_refs": ["IT_ADMIN"],
            "permission_refs": ["identity.accounts.manage"],
            "scope_refs": ["scope.identity.manage"],
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "identity-admin@andara.local", "password": "StrongPass!123"},
    )
    principal = login.json()["principal"]
    response = await client.post(
        "/api/v1/identity/accounts",
        headers={
            "Authorization": f"Bearer {login.json()['access_token']}",
            "X-Correlation-ID": "corr_identity_provision_001",
        },
        json={
            "email": "provisioned@andara.local",
            "password": "StrongPass!456",
            "display_name": "Provisioned Account",
            "workspace_id": "workspace_operations",
            "role_refs": ["WORKSPACE_MEMBER"],
            "permission_refs": ["documents.read"],
            "scope_refs": ["scope.workspace.operations"],
            "data_scope": "WORKSPACE",
        },
    )

    assert response.status_code == 201
    assert response.json()["actor"]["actor_id"] != principal["actor"]["actor_id"]

    audit_events = client._transport.app.state.identity_audit.list_events(  # type: ignore[attr-defined]
        tenant_id="tenant_default"
    )
    event = audit_events[0]
    assert event.event_type == "identity.account.provisioned"
    assert event.actor_id == principal["actor"]["actor_id"]


@pytest.mark.asyncio
async def test_account_provisioning_denies_missing_permission(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "identity-member@andara.local",
            "password": "StrongPass!123",
            "display_name": "Identity Member",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_operations",
            "role_refs": ["WORKSPACE_MEMBER"],
            "scope_refs": ["scope.identity.manage"],
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "identity-member@andara.local", "password": "StrongPass!123"},
    )
    response = await client.post(
        "/api/v1/identity/accounts",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json={
            "email": "denied@andara.local",
            "password": "StrongPass!456",
            "display_name": "Denied Account",
            "workspace_id": "workspace_operations",
            "role_refs": ["WORKSPACE_MEMBER"],
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "AUTHORIZATION_DENIED"


@pytest.mark.asyncio
async def test_account_provisioning_denies_cross_tenant_boundary(
    client: httpx.AsyncClient,
) -> None:
    await _register_identity(
        client,
        email="tenant-admin@andara.local",
        tenant_id="tenant_a",
        organization_id="org_a",
        workspace_id="workspace_a",
        permissions=["identity.accounts.manage"],
    )
    await _register_identity(
        client,
        email="tenant-b@andara.local",
        tenant_id="tenant_b",
        organization_id="org_b",
        workspace_id="workspace_b",
    )

    response = await client.post(
        "/api/v1/identity/accounts",
        headers=await _login_headers(client, "tenant-admin@andara.local"),
        json={
            **_provision_payload(
                workspace_id="workspace_a",
                email="cross-tenant@andara.local",
            ),
            "tenant_id": "tenant_b",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_account_provisioning_denies_cross_organization_boundary(
    client: httpx.AsyncClient,
) -> None:
    await _register_identity(
        client,
        email="org-admin@andara.local",
        tenant_id="tenant_shared",
        organization_id="org_a",
        workspace_id="workspace_a",
        permissions=["identity.accounts.manage"],
    )
    await _register_identity(
        client,
        email="org-b@andara.local",
        tenant_id="tenant_shared",
        organization_id="org_b",
        workspace_id="workspace_b",
    )

    response = await client.post(
        "/api/v1/identity/accounts",
        headers=await _login_headers(client, "org-admin@andara.local"),
        json={
            **_provision_payload(
                workspace_id="workspace_a",
                email="cross-org@andara.local",
            ),
            "organization_id": "org_b",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_account_provisioning_denies_foreign_workspace(
    client: httpx.AsyncClient,
) -> None:
    await _register_identity(
        client,
        email="workspace-admin@andara.local",
        tenant_id="tenant_shared",
        organization_id="org_a",
        workspace_id="workspace_a",
        permissions=["identity.accounts.manage"],
    )
    await _register_identity(
        client,
        email="foreign-workspace@andara.local",
        tenant_id="tenant_shared",
        organization_id="org_b",
        workspace_id="workspace_b",
    )

    response = await client.post(
        "/api/v1/identity/accounts",
        headers=await _login_headers(client, "workspace-admin@andara.local"),
        json=_provision_payload(
            workspace_id="workspace_b",
            email="foreign-workspace-target@andara.local",
        ),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "AUTHORITY_BOUNDARY_CONFLICT"


@pytest.mark.asyncio
async def test_membership_assignment_denies_cross_organization_actor(
    client: httpx.AsyncClient,
) -> None:
    await _register_identity(
        client,
        email="membership-admin@andara.local",
        tenant_id="tenant_shared",
        organization_id="org_a",
        workspace_id="workspace_a",
        permissions=["identity.memberships.manage"],
    )
    foreign = await _register_identity(
        client,
        email="foreign-actor@andara.local",
        tenant_id="tenant_shared",
        organization_id="org_b",
        workspace_id="workspace_b",
    )

    response = await client.post(
        f"/api/v1/identity/actors/{foreign['actor']['actor_id']}/memberships",
        headers=await _login_headers(client, "membership-admin@andara.local"),
        json={
            "workspace_id": "workspace_a",
            "role_refs": ["WORKSPACE_MEMBER"],
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "MEMBERSHIP_CONFLICT"


@pytest.mark.asyncio
async def test_multi_workspace_login_requires_explicit_selection_and_revocation_fails_closed(
    client: httpx.AsyncClient,
) -> None:
    admin = await _register_identity(
        client,
        email="multi-workspace@andara.local",
        tenant_id="tenant_shared",
        organization_id="org_shared",
        workspace_id="workspace_alpha",
        permissions=["identity.memberships.manage", "identity.memberships.read"],
    )
    await _register_identity(
        client,
        email="workspace-beta-seed@andara.local",
        tenant_id="tenant_shared",
        organization_id="org_shared",
        workspace_id="workspace_beta",
    )
    actor_id = admin["actor"]["actor_id"]
    initial_headers = await _login_headers(client, "multi-workspace@andara.local")
    assigned = await client.post(
        f"/api/v1/identity/actors/{actor_id}/memberships",
        headers=initial_headers,
        json={
            "workspace_id": "workspace_beta",
            "role_refs": ["WORKSPACE_LEAD"],
            "permission_refs": ["identity.memberships.manage"],
            "scope_refs": ["scope.identity.manage"],
        },
    )
    assert assigned.status_code == 201

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "multi-workspace@andara.local", "password": "StrongPass!123"},
    )
    assert login.status_code == 200
    body = login.json()
    assert body["principal"]["active_workspace"] is None
    token = body["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    selected = await client.put(
        "/api/v1/auth/active-workspace",
        headers=headers,
        json={"workspace_id": "workspace_beta"},
    )
    assert selected.status_code == 200
    assert selected.json()["actor_id"] == actor_id

    whoami = await client.get("/api/v1/auth/whoami", headers=headers)
    assert whoami.status_code == 200
    active = whoami.json()["active_workspace"]
    assert active["workspace"]["workspace_id"] == "workspace_beta"
    assert active["role_refs"] == ["WORKSPACE_LEAD"]
    assert whoami.json()["actor"]["actor_id"] == actor_id

    revoked = await client.delete(
        f"/api/v1/identity/actors/{actor_id}/memberships/workspace_beta",
        headers=initial_headers,
    )
    assert revoked.status_code == 204

    after_revocation = await client.get("/api/v1/auth/whoami", headers=headers)
    assert after_revocation.status_code == 200
    assert after_revocation.json()["active_workspace"] is None
    assert {
        item["workspace"]["workspace_id"]
        for item in after_revocation.json()["workspace_access"]
    } == {"workspace_alpha"}
    protected = await client.get(
        f"/api/v1/identity/actors/{actor_id}/access",
        headers=headers,
    )
    assert protected.status_code == 403
    assert protected.json()["code"] == "ACTIVE_WORKSPACE_REQUIRED"


@pytest.mark.asyncio
async def test_production_provisioning_rejects_legacy_role_alias(
    client: httpx.AsyncClient,
) -> None:
    await _register_identity(
        client,
        email="role-admin@andara.local",
        tenant_id="tenant_roles",
        organization_id="org_roles",
        workspace_id="workspace_roles",
        permissions=["identity.accounts.manage"],
    )
    payload = _provision_payload(
        workspace_id="workspace_roles",
        email="legacy-role@andara.local",
    )
    payload["role_refs"] = ["IT_LEAD"]

    response = await client.post(
        "/api/v1/identity/accounts",
        headers=await _login_headers(client, "role-admin@andara.local"),
        json=payload,
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_AUTHORIZATION_ROLE"


@pytest.mark.asyncio
async def test_membership_mutation_rejects_legacy_role_alias(
    client: httpx.AsyncClient,
) -> None:
    admin = await _register_identity(
        client,
        email="membership-role-admin@andara.local",
        tenant_id="tenant_roles",
        organization_id="org_roles",
        workspace_id="workspace_roles",
        permissions=["identity.memberships.manage"],
    )

    response = await client.post(
        f"/api/v1/identity/actors/{admin['actor']['actor_id']}/memberships",
        headers=await _login_headers(client, "membership-role-admin@andara.local"),
        json={
            "workspace_id": "workspace_roles",
            "role_refs": ["IT_LEAD"],
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_AUTHORIZATION_ROLE"


@pytest.mark.asyncio
async def test_admin_manages_multi_workspace_membership_and_account_state(
    client: httpx.AsyncClient,
) -> None:
    admin_registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "access-admin@andara.local",
            "password": "StrongPass!123",
            "display_name": "Access Admin",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_operations",
            "workspace_key": "OPERATIONS",
            "workspace_name": "Operations",
            "role_refs": ["IT_ADMIN"],
            "permission_refs": [
                "identity.accounts.manage",
                "identity.memberships.read",
                "identity.memberships.manage",
            ],
            "scope_refs": ["scope.identity.manage"],
        },
    )
    target_registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "workspace-target@andara.local",
            "password": "StrongPass!123",
            "display_name": "Workspace Target",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_finance",
            "workspace_key": "FINANCE",
            "workspace_name": "Finance",
            "role_refs": ["WORKSPACE_MEMBER"],
            "scope_refs": ["scope.workspace.finance"],
        },
    )
    admin_actor_id = admin_registration.json()["actor"]["actor_id"]
    target_actor_id = target_registration.json()["actor"]["actor_id"]
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "access-admin@andara.local", "password": "StrongPass!123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    membership = {
        "workspace_id": "workspace_finance",
        "role_refs": ["BUSINESS_REVIEWER"],
        "permission_refs": ["finance.read"],
        "scope_refs": ["scope.workspace.finance"],
        "data_scope": "WORKSPACE",
    }

    assigned = await client.post(
        f"/api/v1/identity/actors/{admin_actor_id}/memberships",
        headers=headers,
        json=membership,
    )
    assert assigned.status_code == 201

    access = await client.get(
        f"/api/v1/identity/actors/{admin_actor_id}/access", headers=headers
    )
    assert access.status_code == 200
    assert {
        item["workspace"]["workspace_id"] for item in access.json()["workspace_access"]
    } == {"workspace_operations", "workspace_finance"}

    selected = await client.put(
        "/api/v1/auth/active-workspace",
        headers=headers,
        json={"workspace_id": "workspace_finance"},
    )
    assert selected.status_code == 200
    assert (
        await client.put(
            "/api/v1/auth/active-workspace",
            headers=headers,
            json={"workspace_id": "workspace_operations"},
        )
    ).status_code == 200

    updated = await client.put(
        f"/api/v1/identity/actors/{admin_actor_id}/memberships",
        headers=headers,
        json={**membership, "role_refs": ["WORKSPACE_LEAD"]},
    )
    assert updated.status_code == 200
    assert updated.json()["role_refs"] == ["WORKSPACE_LEAD"]

    suspended = await client.post(
        f"/api/v1/identity/actors/{target_actor_id}/suspend", headers=headers
    )
    assert suspended.status_code == 200
    assert suspended.json() == {"actor_id": target_actor_id, "active": False}
    target_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "workspace-target@andara.local", "password": "StrongPass!123"},
    )
    assert target_login.status_code == 401

    revoked = await client.delete(
        f"/api/v1/identity/actors/{admin_actor_id}/memberships/workspace_finance",
        headers=headers,
    )
    assert revoked.status_code == 204
    assert (
        await client.put(
            "/api/v1/auth/active-workspace",
            headers=headers,
            json={"workspace_id": "workspace_finance"},
        )
    ).status_code == 403
