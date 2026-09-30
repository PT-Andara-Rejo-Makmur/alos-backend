from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import httpx
import pytest

from alos.config import Settings
from alos.domains.crud import DOMAIN_RESOURCES
from alos.main import create_app

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_ROOT = BACKEND_ROOT.parent / "alos-contracts"


def _database_url(name: str) -> str:
    source = os.environ.get(
        "ALOS_TEST_DATABASE_URL",
        "postgresql+asyncpg://alos:alos@127.0.0.1:5432/alos_test",
    )
    parts = urlsplit(source)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


async def _create_test_database(name: str) -> None:
    admin_url = _database_url("postgres").replace("+asyncpg", "")
    admin = await asyncpg.connect(admin_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()


def _upgrade_database(database_url: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": database_url},
        check=True,
        capture_output=True,
        text=True,
    )


async def _drop_test_database(name: str) -> None:
    admin = await asyncpg.connect(_database_url("postgres").replace("+asyncpg", ""))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _register_and_login(
    client: httpx.AsyncClient,
    *,
    email: str,
    permissions: list[str],
    roles: list[str] | None = None,
    workspace_id: str = "workspace_it",
) -> dict[str, str]:
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass!123",
            "display_name": "Domain API Test",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": workspace_id,
            "workspace_key": workspace_id.removeprefix("workspace_"),
            "workspace_name": f"{workspace_id.removeprefix('workspace_').upper()} Workspace",
            "workspace_type": "IT_OPERATIONS" if workspace_id == "workspace_it" else "BUSINESS",
            "role_refs": roles or ["IT_ADMIN"],
            "permission_refs": permissions,
            "scope_refs": ["scope.domain.test"],
            "data_scope": "WORKSPACE",
        },
    )
    assert registered.status_code == 201
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "StrongPass!123"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_domain_resource_catalog_covers_migrations_0012_to_0021() -> None:
    assert len(DOMAIN_RESOURCES) == 95
    assert ("core", "projects") in DOMAIN_RESOURCES
    assert ("finance", "bank_accounts") in DOMAIN_RESOURCES
    assert ("hr", "employees") in DOMAIN_RESOURCES
    assert ("legal", "contracts") in DOMAIN_RESOURCES
    assert ("sales", "customers") in DOMAIN_RESOURCES
    assert ("marketing", "campaigns") in DOMAIN_RESOURCES
    assert ("property", "property_units") in DOMAIN_RESOURCES
    assert ("it", "systems") in DOMAIN_RESOURCES
    assert ("genesis", "uat_gates") in DOMAIN_RESOURCES
    assert ("strategy", "plans") not in DOMAIN_RESOURCES


@pytest.mark.asyncio
async def test_domain_data_api_rejects_missing_read_and_write_permissions(
    client: httpx.AsyncClient,
) -> None:
    headers = await _register_and_login(
        client,
        email="domain-no-permission@andara.local",
        permissions=[],
    )

    read = await client.get("/api/v1/domains/finance/bank_accounts", headers=headers)
    create = await client.post(
        "/api/v1/domains/finance/bank_accounts",
        headers=headers,
        json={"account_name": "Main account", "bank_name": "Example Bank"},
    )

    assert read.status_code == 403
    assert read.json()["code"] == "AUTHORIZATION_DENIED"
    assert create.status_code == 403
    assert create.json()["code"] == "AUTHORIZATION_DENIED"


@pytest.mark.asyncio
async def test_domain_data_api_uses_action_specific_permissions(
    client: httpx.AsyncClient,
) -> None:
    headers = await _register_and_login(
        client,
        email="domain-reader@andara.local",
        permissions=["finance.read"],
    )

    create = await client.post(
        "/api/v1/domains/finance/bank_accounts",
        headers=headers,
        json={"account_name": "Main account", "bank_name": "Example Bank"},
    )

    assert create.status_code == 403
    assert create.json()["code"] == "AUTHORIZATION_DENIED"


@pytest.mark.asyncio
async def test_global_navigation_catalog_requires_it_account_admin(
    client: httpx.AsyncClient,
) -> None:
    headers = await _register_and_login(
        client,
        email="navigation-non-admin@andara.local",
        permissions=["identity.accounts.manage"],
        roles=["DIVISION_MEMBER"],
    )

    response = await client.get("/api/v1/domains/navigation/navigation_items", headers=headers)

    assert response.status_code == 403
    assert response.json()["code"] == "AUTHORIZATION_DENIED"


@pytest.mark.asyncio
async def test_domain_crud_round_trip_and_shared_workspace_scope() -> None:
    database_name = f"alos_domain_crud_{uuid.uuid4().hex[:12]}"
    await _create_test_database(database_name)
    database_url = _database_url(database_name)
    client: httpx.AsyncClient | None = None
    app = None
    try:
        _upgrade_database(database_url)
        settings = Settings(
            _env_file=None,
            APP_ENV="test",
            DATABASE_URL=database_url,
            ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
            GENESIS_BASE_URL="http://genesis.test",
            GENESIS_INTERNAL_TOKEN="domain-crud-test-token",  # noqa: S106
        )
        app = create_app(settings)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        headers = await _register_and_login(
            client,
            email="domain-crud-round-trip@andara.local",
            permissions=[
                "finance.read",
                "finance.write",
                "finance.delete",
                "work.read",
                "work.write",
                "work.delete",
            ],
        )
        whoami = await client.get("/api/v1/auth/whoami", headers=headers)
        actor_id = whoami.json()["actor"]["actor_id"]
        postgres_url = database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
        postgres = await asyncpg.connect(postgres_url)
        try:
            await postgres.execute(
                """
                INSERT INTO core.actors (actor_id, tenant_id, organization_id, display_name, active)
                VALUES ($1, 'tenant_default', 'org_default', 'CRUD Admin', true)
                """,
                actor_id,
            )
            await postgres.execute(
                """
                INSERT INTO core.workspace_memberships (
                    actor_id, workspace_id, tenant_id, organization_id, roles,
                    permission_refs, scope_refs, data_scope, active, created_at,
                    effective_at, expires_at, updated_at
                )
                VALUES (
                    $1, 'workspace_it', 'tenant_default', 'org_default',
                    '["IT_ADMIN"]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                    'WORKSPACE', true, now(), now(), NULL, now()
                )
                """,
                actor_id,
            )
        finally:
            await postgres.close()

        created = await client.post(
            "/api/v1/domains/finance/bank_accounts",
            headers=headers,
            json={"account_name": "Operating account", "bank_name": "Example Bank"},
        )
        assert created.status_code == 201, created.text
        account = created.json()
        assert account["workspace_id"] == "workspace_it"

        listed = await client.get("/api/v1/domains/finance/bank_accounts", headers=headers)
        assert listed.status_code == 200
        assert any(row["bank_account_id"] == account["bank_account_id"] for row in listed.json())

        updated = await client.patch(
            f"/api/v1/domains/finance/bank_accounts/{account['bank_account_id']}",
            headers=headers,
            json={"bank_name": "Updated Bank"},
        )
        assert updated.status_code == 200
        assert updated.json()["bank_name"] == "Updated Bank"

        created_project = await client.post(
            "/api/v1/projects",
            headers=headers,
            json={"code": "API-PROJECT", "name": "API Project"},
        )
        assert created_project.status_code == 201, created_project.text
        project_id = created_project.json()["project_id"]
        assert created_project.json()["workspace_ids"] == ["workspace_it"]

        for method, path, payload in (
            (
                "POST",
                "/api/v1/domains/shared/work_approvals",
                {"subject_type": "PROJECT", "subject_id": project_id, "status": "APPROVED"},
            ),
            (
                "PATCH",
                "/api/v1/domains/shared/work_approvals/approval_123",
                {"decision": "APPROVED", "decided_at": "2026-09-30T00:00:00Z"},
            ),
            ("DELETE", "/api/v1/domains/shared/work_approvals/approval_123", None),
        ):
            rejected = await client.request(method, path, headers=headers, json=payload)
            assert rejected.status_code == 409, rejected.text
            assert rejected.json()["code"] == "APPROVAL_LIFECYCLE_REQUIRES_DEDICATED_API"

        postgres = await asyncpg.connect(postgres_url)
        try:
            assert await postgres.fetchval("SELECT count(*) FROM core.work_approvals") == 0
        finally:
            await postgres.close()

        hr_headers = await _register_and_login(
            client,
            email="domain-crud-hr@andara.local",
            permissions=["work.read", "finance.read", "finance.write"],
            roles=["DIVISION_MEMBER"],
            workspace_id="workspace_hr",
        )
        hr_actor_id = (await client.get("/api/v1/auth/whoami", headers=hr_headers)).json()["actor"][
            "actor_id"
        ]
        postgres = await asyncpg.connect(postgres_url)
        try:
            await postgres.execute(
                """
                INSERT INTO core.actors (actor_id, tenant_id, organization_id, display_name, active)
                VALUES ($1, 'tenant_default', 'org_default', 'HR User', true)
                """,
                hr_actor_id,
            )
            await postgres.execute(
                """
                INSERT INTO core.workspace_memberships (
                    actor_id, workspace_id, tenant_id, organization_id, roles,
                    permission_refs, scope_refs, data_scope, active, created_at,
                    effective_at, expires_at, updated_at
                )
                VALUES (
                    $1, 'workspace_hr', 'tenant_default', 'org_default',
                    '["DIVISION_MEMBER"]'::jsonb,
                    '["work.read","finance.read","finance.write"]'::jsonb,
                    '[]'::jsonb, 'WORKSPACE', true, now(), now(), NULL, now()
                )
                """,
                hr_actor_id,
            )
        finally:
            await postgres.close()

        hidden_project = await client.get(
            f"/api/v1/domains/shared/projects/{project_id}", headers=hr_headers
        )
        hidden_finance_rows = await client.get(
            "/api/v1/domains/finance/bank_accounts", headers=hr_headers
        )
        assert hidden_project.status_code == 404
        assert hidden_finance_rows.status_code == 200
        assert hidden_finance_rows.json() == []
        cross_workspace_reference = await client.post(
            "/api/v1/domains/finance/bank_transactions",
            headers=hr_headers,
            json={
                "bank_account_id": account["bank_account_id"],
                "transaction_date": "2026-09-28",
                "direction": "IN",
                "amount": "10.00",
            },
        )
        assert cross_workspace_reference.status_code == 404
        assert cross_workspace_reference.json()["code"] == "DOMAIN_REFERENCE_NOT_FOUND"

        forged_scope = await client.post(
            "/api/v1/domains/finance/bank_accounts",
            headers=headers,
            json={
                "account_name": "Forged account",
                "bank_name": "Example Bank",
                "tenant_id": "tenant_attacker",
            },
        )
        assert forged_scope.status_code == 422
        assert forged_scope.json()["code"] == "INVALID_DOMAIN_FIELDS"

        deleted_project = await client.delete(
            f"/api/v1/domains/shared/projects/{project_id}", headers=headers
        )
        assert deleted_project.status_code == 409
        assert deleted_project.json()["code"] == "WORK_MUTATION_REQUIRES_DEDICATED_API"
        retained_project = await client.get(
            f"/api/v1/projects/{project_id}", headers=headers
        )
        assert retained_project.status_code == 200

        deleted_account = await client.delete(
            f"/api/v1/domains/finance/bank_accounts/{account['bank_account_id']}",
            headers=headers,
        )
        assert deleted_account.status_code == 204

        # -------------------------------------------------------------
        # Pengujian Antar Divisi: Sales vs Marketing & Workspace Switch
        # -------------------------------------------------------------
        sales_headers = await _register_and_login(
            client,
            email="sales-lead@andara.local",
            permissions=["sales.read", "sales.write", "sales.delete"],
            roles=["DIVISION_LEAD"],
            workspace_id="workspace_sales",
        )
        sales_actor_id = (await client.get("/api/v1/auth/whoami", headers=sales_headers)).json()[
            "actor"
        ]["actor_id"]

        marketing_headers = await _register_and_login(
            client,
            email="marketing-lead@andara.local",
            permissions=["marketing.read", "marketing.write", "marketing.delete"],
            roles=["DIVISION_LEAD"],
            workspace_id="workspace_marketing",
        )
        marketing_actor_id = (
            await client.get("/api/v1/auth/whoami", headers=marketing_headers)
        ).json()["actor"]["actor_id"]
        await app.state.auth_service.assign_membership(
            sales_actor_id,
            {
                "workspace_id": "workspace_marketing",
                "role_refs": ["DIVISION_MEMBER"],
                "permission_refs": ["marketing.read"],
                "scope_refs": [],
                "data_scope": "WORKSPACE",
            },
            tenant_id="tenant_default",
            organization_id="org_default",
        )

        postgres = await asyncpg.connect(postgres_url)
        try:
            await postgres.execute(
                """
                INSERT INTO core.workspaces (
                    workspace_id, tenant_id, organization_id, name, workspace_key,
                    workspace_type, division_code, active
                )
                VALUES (
                    'workspace_marketing', 'tenant_default', 'org_default',
                    'Marketing Workspace', 'marketing', 'BUSINESS', 'MARKETING', true
                )
                """
            )
            await postgres.execute(
                """
                INSERT INTO core.actors (actor_id, tenant_id, organization_id, display_name, active)
                VALUES
                    ($1, 'tenant_default', 'org_default', 'Sales Lead', true),
                    ($2, 'tenant_default', 'org_default', 'Marketing Lead', true)
                """,
                sales_actor_id,
                marketing_actor_id,
            )
            await postgres.execute(
                """
                INSERT INTO core.workspace_memberships (
                    actor_id, workspace_id, tenant_id, organization_id, roles,
                    permission_refs, scope_refs, data_scope, active, created_at,
                    effective_at, expires_at, updated_at
                )
                VALUES
                    ($1, 'workspace_sales', 'tenant_default', 'org_default',
                     '["DIVISION_LEAD"]'::jsonb,
                     '["sales.read","sales.write","sales.delete"]'::jsonb,
                     '[]'::jsonb, 'WORKSPACE', true, now(), now(), NULL, now()),
                    ($2, 'workspace_marketing', 'tenant_default', 'org_default',
                     '["DIVISION_LEAD"]'::jsonb,
                     '["marketing.read","marketing.write","marketing.delete"]'::jsonb,
                     '[]'::jsonb, 'WORKSPACE', true, now(), now(), NULL, now()),
                    -- Tambahkan keanggotaan kedua untuk sales_actor (Multi-Workspace)
                    ($1, 'workspace_marketing', 'tenant_default', 'org_default',
                     '["DIVISION_MEMBER"]'::jsonb, '["marketing.read"]'::jsonb,
                     '[]'::jsonb, 'WORKSPACE', true, now(), now(), NULL, now())
                """,
                sales_actor_id,
                marketing_actor_id,
            )
        finally:
            await postgres.close()

        # 1. Sales buat data Customer
        created_customer = await client.post(
            "/api/v1/domains/sales/customers",
            headers=sales_headers,
            json={
                "customer_code": "CUST-001",
                "name": "PT Mitra Abadi",
                "customer_type": "CORPORATE",
                "status": "ACTIVE",
            },
        )
        assert created_customer.status_code == 201
        customer_id = created_customer.json()["customer_id"]

        # 2. Marketing coba intip customer Sales -> Harus kosong [] / 404
        marketing_view_customers = await client.get(
            "/api/v1/domains/sales/customers", headers=marketing_headers
        )
        # Tidak punya permission sales.read -> 403
        assert marketing_view_customers.status_code == 403

        # 3. Marketing buat Campaign
        created_campaign = await client.post(
            "/api/v1/domains/marketing/campaigns",
            headers=marketing_headers,
            json={"name": "Promo Q4", "campaign_type": "DIGITAL", "status": "ACTIVE"},
        )
        assert created_campaign.status_code == 201
        campaign_id = created_campaign.json()["campaign_id"]

        # Sales coba baca campaign di workspace_sales -> 403 (izin hanya sales.*).
        sales_view_campaigns = await client.get(
            f"/api/v1/domains/marketing/campaigns/{campaign_id}", headers=sales_headers
        )
        assert sales_view_campaigns.status_code == 403

        # 5. Uji Switch Workspace: Sales pindah workspace aktif ke workspace_marketing
        switch_resp = await client.put(
            "/api/v1/auth/active-workspace",
            headers=sales_headers,
            json={"workspace_id": "workspace_marketing"},
        )
        assert switch_resp.status_code == 200

        # Sekarang Sales di workspace_marketing dapat membaca campaign Marketing
        sales_read_campaign_after_switch = await client.get(
            f"/api/v1/domains/marketing/campaigns/{campaign_id}", headers=sales_headers
        )
        assert sales_read_campaign_after_switch.status_code == 200
        assert sales_read_campaign_after_switch.json()["name"] == "Promo Q4"

        # Tidak dapat membaca Customer Sales setelah pindah ke workspace marketing.
        sales_read_old_customer = await client.get(
            f"/api/v1/domains/sales/customers/{customer_id}", headers=sales_headers
        )
        # Di workspace_marketing izinnya marketing.read, tidak punya sales.read -> 403
        assert sales_read_old_customer.status_code == 403
    finally:
        if client is not None:
            await client.aclose()
        if app is not None:
            await app.state.database.dispose()
        await _drop_test_database(database_name)
