import hashlib
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from alos.contracts import CanonicalContractCatalog

DEFAULT_CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"
CONTRACTS_ROOT = Path(os.environ.get("ALOS_CONTRACTS_PATH", str(DEFAULT_CONTRACTS_ROOT)))


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
        "employee_id": "employee_" + email.split("@", 1)[0].replace("-", "_"),
        "workspace_id": workspace_id,
        "role_refs": ["DIVISION_MEMBER"],
        "effective_at": "2026-01-01T00:00:00Z",
    }


def _seed_test_employee(
    client: httpx.AsyncClient,
    employee_id: str,
    *,
    tenant_id: str,
    organization_id: str,
    full_name: str = "Provisioned Employee",
    employment_status: str = "ACTIVE",
) -> None:
    app = client._transport.app  # type: ignore[attr-defined]
    app.state.auth_service._repository.add_test_employee(
        employee_id,
        tenant_id=tenant_id,
        organization_id=organization_id,
        full_name=full_name,
        employment_status=employment_status,
        email=employee_id.removeprefix("employee_").replace("_", "-") + "@example.test",
        join_date=datetime.now(UTC).date(),
    )


@pytest.mark.asyncio
async def test_register_and_login_round_trip(client: httpx.AsyncClient) -> None:
    register_response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "ops@example.test",
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
    assert payload["email"] == "ops@example.test"
    assert payload["workspace_access"][0]["workspace"]["workspace_id"] == "workspace_operations"
    assert payload["actor"]["actor_id"]

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "ops@example.test", "password": "StrongPass!123"},
    )

    assert login_response.status_code == 200
    body = login_response.json()
    assert body["token_type"] == "bearer"  # noqa: S105 - OAuth2 token type constant, not a password
    active = body["principal"]["active_workspace"]
    assert active["workspace"]["workspace_id"] == "workspace_operations"
    assert "scope.workspace.operations" in active["scope_refs"]
    assert active["role_refs"] == ["DIVISION_LEAD"]


@pytest.mark.asyncio
async def test_whoami_returns_authenticated_principal(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "whoami@example.test",
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
        json={"email": "whoami@example.test", "password": "StrongPass!123"},
    )
    token = login_response.json()["access_token"]

    whoami_response = await client.get(
        "/api/v1/auth/whoami",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert whoami_response.status_code == 200
    whoami = whoami_response.json()
    assert whoami["email"] == "whoami@example.test"
    assert whoami["active_workspace"]["workspace"]["workspace_id"] == "workspace_operations"


@pytest.mark.asyncio
async def test_workspace_listing_and_selection_fail_closed(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "workspace@example.test",
            "password": "StrongPass!123",
            "display_name": "Workspace User",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_operations",
            "role_refs": ["DIVISION_MEMBER"],
            "permission_refs": ["documents.read"],
            "scope_refs": ["scope.workspace.operations"],
            "data_scope": "WORKSPACE",
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "workspace@example.test", "password": "StrongPass!123"},
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
            "email": "logout@example.test",
            "password": "StrongPass!123",
            "display_name": "Logout User",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_operations",
            "role_refs": ["DIVISION_MEMBER"],
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "logout@example.test", "password": "StrongPass!123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/auth/whoami", headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_unknown_credentials(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.test", "password": "wrongpassword"},
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
            "email": "identity-admin@example.test",
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
        json={"email": "identity-admin@example.test", "password": "StrongPass!123"},
    )
    principal = login.json()["principal"]
    employee_id = _provision_payload(
        workspace_id="workspace_operations", email="provisioned@example.test"
    )["employee_id"]
    _seed_test_employee(
        client,
        employee_id,
        tenant_id="tenant_default",
        organization_id="org_default",
    )
    response = await client.post(
        "/api/v1/identity/accounts",
        headers={
            "Authorization": f"Bearer {login.json()['access_token']}",
            "X-Correlation-ID": "corr_identity_provision_001",
        },
        json=_provision_payload(
            workspace_id="workspace_operations", email="provisioned@example.test"
        ),
    )

    assert response.status_code == 201
    assert response.json()["actor_id"] != principal["actor"]["actor_id"]

    audit_events = client._transport.app.state.identity_audit.list_events(  # type: ignore[attr-defined]
        tenant_id="tenant_default"
    )
    event = next(
        event for event in audit_events if event.event_type == "identity.account.provisioned"
    )
    assert event.event_type == "identity.account.provisioned"
    assert event.actor_id == principal["actor"]["actor_id"]


async def test_final_lifecycle_audit_vocabulary_and_reset_privacy(
    client: httpx.AsyncClient,
) -> None:
    await _register_identity(
        client,
        email="audit-admin@example.test",
        tenant_id="tenant_audit",
        organization_id="org_audit",
        workspace_id="workspace_audit",
        permissions=[
            "identity.accounts.manage",
            "identity.accounts.suspend",
            "identity.accounts.activate",
            "identity.sessions.revoke",
        ],
    )
    headers = await _login_headers(client, "audit-admin@example.test")
    payload = _provision_payload(
        workspace_id="workspace_audit", email="audit-employee@example.test"
    )
    _seed_test_employee(
        client, payload["employee_id"], tenant_id="tenant_audit", organization_id="org_audit"
    )
    provisioned = await client.post("/api/v1/identity/accounts", headers=headers, json=payload)
    assert provisioned.status_code == 201
    assert provisioned.json()["email"] == "audit-employee@example.test"
    actor_id = provisioned.json()["actor_id"]
    app = client._transport.app  # type: ignore[attr-defined]
    old_token = app.state.test_activation_sink["audit-employee@example.test"]
    resent = await client.post(
        f"/api/v1/identity/actors/{actor_id}/activation/resend", headers=headers, json={}
    )
    assert resent.status_code == 200
    token = app.state.test_activation_sink["audit-employee@example.test"]
    assert token != old_token
    activated = await client.post(
        "/api/v1/identity/activate",
        json={
            "token": token,
            "password": "EmployeePass!123",
            "password_confirmation": "EmployeePass!123",
        },
    )
    assert activated.status_code == 200
    for action in ("suspend", "activate"):
        changed = await client.post(
            f"/api/v1/identity/actors/{actor_id}/{action}",
            headers=headers,
            json={"reason": "Audit test"},
        )
        assert changed.status_code == 200
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "audit-employee@example.test", "password": "EmployeePass!123"},
    )
    assert login.status_code == 200
    sessions = await client.get(f"/api/v1/identity/actors/{actor_id}/sessions", headers=headers)
    revoked = await client.delete(
        f"/api/v1/identity/actors/{actor_id}/sessions/{sessions.json()[0]['session_id']}",
        headers=headers,
    )
    assert revoked.status_code == 204
    known = await client.post(
        "/api/v1/auth/password-reset/request", json={"email": "audit-employee@example.test"}
    )
    unknown = await client.post(
        "/api/v1/auth/password-reset/request", json={"email": "unknown@example.test"}
    )
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    reset_token = app.state.test_activation_sink["audit-employee@example.test"]
    confirmed = await client.post(
        "/api/v1/auth/password-reset/confirm",
        json={
            "token": reset_token,
            "password": "NewPassword!123",
            "password_confirmation": "NewPassword!123",
        },
    )
    assert confirmed.status_code == 200
    events = app.state.identity_audit.list_events(tenant_id="tenant_audit")
    event_types = [event.event_type for event in events]
    assert event_types.count("identity.account.activated") == 1
    assert event_types.count("identity.account.reactivated") == 1
    assert {
        "identity.account.provisioned",
        "identity.activation.challenge_issued",
        "identity.activation.resent",
        "identity.account.suspended",
        "auth.session.revoked",
    } <= set(event_types)
    reset_events = app.state.identity_audit.list_events(tenant_id="SYSTEM")
    assert {event.event_type for event in reset_events} == {
        "auth.password_reset.requested",
        "auth.password_reset.completed",
        "auth.password.changed",
    }
    for event in reset_events:
        assert event.entity_type == "auth"
        assert event.entity_id == "password_reset_request"
        assert event.actor_id == "anonymous"
        assert event.metadata == {}
        assert "@" not in str(event)
        assert reset_token not in str(event)


@pytest.mark.asyncio
async def test_employee_activation_owns_password_setup(client: httpx.AsyncClient) -> None:
    await _register_identity(
        client,
        email="activation-admin@example.test",
        tenant_id="tenant_activation",
        organization_id="org_activation",
        workspace_id="workspace_activation",
        permissions=["identity.accounts.manage"],
    )
    payload = _provision_payload(
        workspace_id="workspace_activation", email="new-employee@example.test"
    )
    _seed_test_employee(
        client,
        payload["employee_id"],
        tenant_id="tenant_activation",
        organization_id="org_activation",
        full_name="New Employee",
    )
    provisioned = await client.post(
        "/api/v1/identity/accounts",
        headers=await _login_headers(client, "activation-admin@example.test"),
        json=payload,
    )
    assert provisioned.status_code == 201
    assert provisioned.json()["activation_state"] == "PENDING"

    denied_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "new-employee@example.test", "password": "EmployeePass!123"},
    )
    assert denied_login.status_code == 401

    app = client._transport.app  # type: ignore[attr-defined]
    token = app.state.test_activation_sink["new-employee@example.test"]
    activation_payload = {
        "token": token,
        "password": "EmployeePass!123",
        "password_confirmation": "EmployeePass!123",
    }
    invalid = await client.post(
        "/api/v1/identity/activate",
        json={**activation_payload, "token": "unrecognized-activation-credential"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "ACTIVATION_CHALLENGE_INVALID"
    assert token not in invalid.text
    activation = await client.post(
        "/api/v1/identity/activate",
        json=activation_payload,
    )
    assert activation.status_code == 200
    assert activation.json() == {
        "actor_id": provisioned.json()["actor_id"],
        "activation_state": "ACTIVATED",
    }
    assert (
        CanonicalContractCatalog(CONTRACTS_ROOT).validate(
            "https://schemas.alos.dev/v1/identity/activate-account-response.schema.json",
            activation.json(),
        )
        == activation.json()
    )
    reused = await client.post("/api/v1/identity/activate", json=activation_payload)
    assert reused.status_code == 422
    assert reused.json()["code"] == "ACTIVATION_CHALLENGE_INVALID"
    assert token not in reused.text
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "new-employee@example.test", "password": "EmployeePass!123"},
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_expired_activation_credential_fails_safely(client: httpx.AsyncClient) -> None:
    await _register_identity(
        client,
        email="expiry-admin@example.test",
        tenant_id="tenant_expiry",
        organization_id="org_expiry",
        workspace_id="workspace_expiry",
        permissions=["identity.accounts.manage"],
    )
    payload = _provision_payload(
        workspace_id="workspace_expiry", email="expiry-employee@example.test"
    )
    _seed_test_employee(
        client,
        payload["employee_id"],
        tenant_id="tenant_expiry",
        organization_id="org_expiry",
    )
    provisioned = await client.post(
        "/api/v1/identity/accounts",
        headers=await _login_headers(client, "expiry-admin@example.test"),
        json=payload,
    )
    assert provisioned.status_code == 201
    app = client._transport.app  # type: ignore[attr-defined]
    token = app.state.test_activation_sink["expiry-employee@example.test"]
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    app.state.auth_service._repository._activation_challenges[token_hash] = (
        provisioned.json()["actor_id"],
        datetime.now(UTC) - timedelta(seconds=1),
    )
    expired = await client.post(
        "/api/v1/identity/activate",
        json={
            "token": token,
            "password": "EmployeePass!123",
            "password_confirmation": "EmployeePass!123",
        },
    )
    assert expired.status_code == 422
    assert expired.json()["code"] == "ACTIVATION_CHALLENGE_INVALID"
    assert token not in expired.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forbidden_field",
    [
        "email",
        "tenant_id",
        "organization_id",
        "workspace_key",
        "workspace_name",
        "workspace_type",
        "password",
        "permission_refs",
        "scope_refs",
        "data_scope",
        "actor_id",
    ],
)
async def test_public_provisioning_rejects_client_authority_metadata(
    client: httpx.AsyncClient, forbidden_field: str
) -> None:
    await _register_identity(
        client,
        email="metadata-admin@example.test",
        tenant_id="tenant_metadata",
        organization_id="org_metadata",
        workspace_id="workspace_metadata",
        permissions=["identity.accounts.manage"],
    )
    response = await client.post(
        "/api/v1/identity/accounts",
        headers=await _login_headers(client, "metadata-admin@example.test"),
        json={
            **_provision_payload(
                workspace_id="workspace_metadata",
                email=f"{forbidden_field}@example.test",
            ),
            forbidden_field: "browser-controlled",
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_FAILED"


@pytest.mark.asyncio
async def test_public_provisioning_derives_boundary_without_inheriting_admin_scope(
    client: httpx.AsyncClient,
) -> None:
    await _register_identity(
        client,
        email="scope-admin@example.test",
        tenant_id="tenant_scope",
        organization_id="org_scope",
        workspace_id="workspace_scope",
        permissions=["identity.accounts.manage"],
    )
    employee_id = _provision_payload(
        workspace_id="workspace_scope", email="least-privilege@example.test"
    )["employee_id"]
    _seed_test_employee(
        client,
        employee_id,
        tenant_id="tenant_scope",
        organization_id="org_scope",
    )
    response = await client.post(
        "/api/v1/identity/accounts",
        headers=await _login_headers(client, "scope-admin@example.test"),
        json=_provision_payload(
            workspace_id="workspace_scope",
            email="least-privilege@example.test",
        ),
    )
    assert response.status_code == 201
    body = response.json()
    assert body["actor_id"]
    assert "tenant_id" not in body
    assert "organization_id" not in body
    assert body["workspace_access"][0]["scope_refs"] == []
    assert body["workspace_access"][0]["permission_refs"] == []


@pytest.mark.asyncio
async def test_identity_admin_catalogs_are_canonical_and_organization_bounded(
    client: httpx.AsyncClient,
) -> None:
    await _register_identity(
        client,
        email="catalog-admin@example.test",
        tenant_id="tenant_catalog",
        organization_id="org_catalog",
        workspace_id="workspace_catalog",
        permissions=["identity.accounts.manage"],
    )
    headers = await _login_headers(client, "catalog-admin@example.test")

    roles = await client.get("/api/v1/identity/assignable-roles", headers=headers)
    assert roles.status_code == 200
    assert roles.json() == ["DIVISION_LEAD", "DIVISION_MEMBER", "EXECUTIVE", "IT_ADMIN"]

    workspaces = await client.get("/api/v1/identity/workspaces", headers=headers)
    assert workspaces.status_code == 200
    assert workspaces.json() == [
        {
            "workspace_id": "workspace_catalog",
            "workspace_key": "workspace_catalog",
            "organization_id": "org_catalog",
            "workspace_name": "workspace_catalog",
            "workspace_type": "BUSINESS",
            "organizational_unit_id": None,
            "division_code": None,
            "active": True,
        }
    ]
    assert "role_refs" not in workspaces.json()[0]

    accounts = await client.get("/api/v1/identity/accounts", headers=headers)
    assert accounts.status_code == 200
    assert [account["email"] for account in accounts.json()] == ["catalog-admin@example.test"]
    assert "password_hash" not in accounts.json()[0]


@pytest.mark.asyncio
async def test_provisioning_candidates_are_minimal_and_backend_filtered(
    client: httpx.AsyncClient,
) -> None:
    await _register_identity(
        client,
        email="candidate-admin@example.test",
        tenant_id="tenant_candidate",
        organization_id="org_candidate",
        workspace_id="workspace_candidate",
        permissions=["identity.accounts.manage"],
    )
    headers = await _login_headers(client, "candidate-admin@example.test")
    _seed_test_employee(
        client,
        "employee_candidate_available",
        tenant_id="tenant_candidate",
        organization_id="org_candidate",
        full_name="Available Employee",
    )
    _seed_test_employee(
        client,
        "employee_candidate_inactive",
        tenant_id="tenant_candidate",
        organization_id="org_candidate",
        full_name="Inactive Employee",
        employment_status="INACTIVE",
    )
    _seed_test_employee(
        client,
        "employee_candidate_foreign",
        tenant_id="tenant_candidate",
        organization_id="org_elsewhere",
        full_name="Foreign Employee",
    )

    response = await client.get("/api/v1/identity/provisioning-candidates", headers=headers)

    assert response.status_code == 200
    assert response.json() == [
        {
            "employee_id": "employee_candidate_available",
            "employee_number": "employee_candidate_available",
            "full_name": "Available Employee",
            "email": "candidate-available@example.test",
            "department_code": None,
            "position_title": None,
            "employment_status": "ACTIVE",
            "linkage_state": "AVAILABLE",
        }
    ]


@pytest.mark.asyncio
async def test_account_provisioning_denies_missing_permission(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register",
        json={
            "email": "identity-member@example.test",
            "password": "StrongPass!123",
            "display_name": "Identity Member",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_operations",
            "role_refs": ["DIVISION_MEMBER"],
            "scope_refs": ["scope.identity.manage"],
        },
    )
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "identity-member@example.test", "password": "StrongPass!123"},
    )
    response = await client.post(
        "/api/v1/identity/accounts",
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        json=_provision_payload(workspace_id="workspace_operations", email="denied@example.test"),
    )

    assert response.status_code == 403
    assert response.json()["code"] == "AUTHORIZATION_DENIED"


@pytest.mark.asyncio
async def test_account_provisioning_denies_cross_tenant_boundary(
    client: httpx.AsyncClient,
) -> None:
    await _register_identity(
        client,
        email="tenant-admin@example.test",
        tenant_id="tenant_a",
        organization_id="org_a",
        workspace_id="workspace_a",
        permissions=["identity.accounts.manage"],
    )
    await _register_identity(
        client,
        email="tenant-b@example.test",
        tenant_id="tenant_b",
        organization_id="org_b",
        workspace_id="workspace_b",
    )

    response = await client.post(
        "/api/v1/identity/accounts",
        headers=await _login_headers(client, "tenant-admin@example.test"),
        json={
            **_provision_payload(
                workspace_id="workspace_a",
                email="cross-tenant@example.test",
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
        email="org-admin@example.test",
        tenant_id="tenant_shared",
        organization_id="org_a",
        workspace_id="workspace_a",
        permissions=["identity.accounts.manage"],
    )
    await _register_identity(
        client,
        email="org-b@example.test",
        tenant_id="tenant_shared",
        organization_id="org_b",
        workspace_id="workspace_b",
    )

    response = await client.post(
        "/api/v1/identity/accounts",
        headers=await _login_headers(client, "org-admin@example.test"),
        json={
            **_provision_payload(
                workspace_id="workspace_a",
                email="cross-org@example.test",
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
        email="workspace-admin@example.test",
        tenant_id="tenant_shared",
        organization_id="org_a",
        workspace_id="workspace_a",
        permissions=["identity.accounts.manage"],
    )
    await _register_identity(
        client,
        email="foreign-workspace@example.test",
        tenant_id="tenant_shared",
        organization_id="org_b",
        workspace_id="workspace_b",
    )

    response = await client.post(
        "/api/v1/identity/accounts",
        headers=await _login_headers(client, "workspace-admin@example.test"),
        json=_provision_payload(
            workspace_id="workspace_b",
            email="foreign-workspace-target@example.test",
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
        email="membership-admin@example.test",
        tenant_id="tenant_shared",
        organization_id="org_a",
        workspace_id="workspace_a",
        permissions=["identity.memberships.manage"],
    )
    foreign = await _register_identity(
        client,
        email="foreign-actor@example.test",
        tenant_id="tenant_shared",
        organization_id="org_b",
        workspace_id="workspace_b",
    )

    response = await client.post(
        f"/api/v1/identity/actors/{foreign['actor']['actor_id']}/memberships",
        headers=await _login_headers(client, "membership-admin@example.test"),
        json={
            "workspace_id": "workspace_a",
            "role_refs": ["DIVISION_MEMBER"],
            "effective_at": "2026-01-01T00:00:00Z",
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
        email="multi-workspace@example.test",
        tenant_id="tenant_shared",
        organization_id="org_shared",
        workspace_id="workspace_alpha",
        permissions=["identity.memberships.manage", "identity.memberships.read"],
    )
    await _register_identity(
        client,
        email="workspace-beta-seed@example.test",
        tenant_id="tenant_shared",
        organization_id="org_shared",
        workspace_id="workspace_beta",
    )
    actor_id = admin["actor"]["actor_id"]
    initial_headers = await _login_headers(client, "multi-workspace@example.test")
    assigned = await client.post(
        f"/api/v1/identity/actors/{actor_id}/memberships",
        headers=initial_headers,
        json={
            "workspace_id": "workspace_beta",
            "role_refs": ["DIVISION_LEAD"],
            "effective_at": "2026-01-01T00:00:00Z",
        },
    )
    assert assigned.status_code == 201

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "multi-workspace@example.test", "password": "StrongPass!123"},
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
    assert active["role_refs"] == ["DIVISION_LEAD"]
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
        item["workspace"]["workspace_id"] for item in after_revocation.json()["workspace_access"]
    } == {"workspace_alpha"}
    protected = await client.get(
        f"/api/v1/identity/actors/{actor_id}/access",
        headers=headers,
    )
    assert protected.status_code == 403
    assert protected.json()["code"] == "ACTIVE_WORKSPACE_REQUIRED"


@pytest.mark.asyncio
async def test_production_provisioning_rejects_unsupported_role(
    client: httpx.AsyncClient,
) -> None:
    await _register_identity(
        client,
        email="role-admin@example.test",
        tenant_id="tenant_roles",
        organization_id="org_roles",
        workspace_id="workspace_roles",
        permissions=["identity.accounts.manage"],
    )
    payload = _provision_payload(
        workspace_id="workspace_roles",
        email="legacy-role@example.test",
    )
    payload["role_refs"] = ["UNSUPPORTED_ROLE"]

    response = await client.post(
        "/api/v1/identity/accounts",
        headers=await _login_headers(client, "role-admin@example.test"),
        json=payload,
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_membership_mutation_rejects_unsupported_role(
    client: httpx.AsyncClient,
) -> None:
    admin = await _register_identity(
        client,
        email="membership-role-admin@example.test",
        tenant_id="tenant_roles",
        organization_id="org_roles",
        workspace_id="workspace_roles",
        permissions=["identity.memberships.manage"],
    )

    response = await client.post(
        f"/api/v1/identity/actors/{admin['actor']['actor_id']}/memberships",
        headers=await _login_headers(client, "membership-role-admin@example.test"),
        json={
            "workspace_id": "workspace_roles",
            "role_refs": ["UNSUPPORTED_ROLE"],
            "effective_at": "2026-01-01T00:00:00Z",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_admin_manages_multi_workspace_membership_and_account_state(
    client: httpx.AsyncClient,
) -> None:
    admin_registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "access-admin@example.test",
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
            "email": "workspace-target@example.test",
            "password": "StrongPass!123",
            "display_name": "Workspace Target",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": "workspace_finance",
            "workspace_key": "FINANCE",
            "workspace_name": "Finance",
            "role_refs": ["DIVISION_MEMBER"],
            "scope_refs": ["scope.workspace.finance"],
        },
    )
    admin_actor_id = admin_registration.json()["actor"]["actor_id"]
    target_actor_id = target_registration.json()["actor"]["actor_id"]
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "access-admin@example.test", "password": "StrongPass!123"},
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    membership = {
        "workspace_id": "workspace_finance",
        "role_refs": ["DIVISION_MEMBER"],
        "effective_at": "2026-01-01T00:00:00Z",
    }

    assigned = await client.post(
        f"/api/v1/identity/actors/{admin_actor_id}/memberships",
        headers=headers,
        json=membership,
    )
    assert assigned.status_code == 201

    access = await client.get(f"/api/v1/identity/actors/{admin_actor_id}/access", headers=headers)
    assert access.status_code == 200
    assert {item["workspace"]["workspace_id"] for item in access.json()["workspace_access"]} == {
        "workspace_operations",
        "workspace_finance",
    }

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
        json={**membership, "role_refs": ["DIVISION_LEAD"]},
    )
    assert updated.status_code == 200
    assert updated.json()["role_refs"] == ["DIVISION_LEAD"]

    suspended = await client.post(
        f"/api/v1/identity/actors/{target_actor_id}/suspend", headers=headers
    )
    assert suspended.status_code == 200
    assert suspended.json() == {"actor_id": target_actor_id, "active": False}
    target_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "workspace-target@example.test", "password": "StrongPass!123"},
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
