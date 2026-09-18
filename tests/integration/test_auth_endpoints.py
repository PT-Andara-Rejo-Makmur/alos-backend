import httpx
import pytest


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
    assert payload["workspace_id"] == "workspace_operations"
    assert payload["actor_id"]

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"email": "ops@andara.local", "password": "StrongPass!123"},
    )

    assert login_response.status_code == 200
    body = login_response.json()
    assert body["token_type"] == "bearer"  # noqa: S105 - OAuth2 token type constant, not a password
    assert body["principal"]["workspace_id"] == "workspace_operations"
    assert "scope.workspace.operations" in body["principal"]["scopes"]


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
    assert whoami_response.json()["email"] == "whoami@andara.local"
    assert whoami_response.json()["workspace_id"] == "workspace_operations"


@pytest.mark.asyncio
async def test_login_rejects_unknown_credentials(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "missing@andara.local", "password": "wrongpassword"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "INVALID_CREDENTIALS"
