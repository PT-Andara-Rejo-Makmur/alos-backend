"""Public document metadata and versions use scoped PostgreSQL authority."""

from __future__ import annotations

import asyncio
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
from alos.main import create_app

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_ROOT = BACKEND_ROOT.parent / "alos-contracts"


def _database_url(name: str) -> str:
    source = os.environ.get(
        "ALOS_TEST_DATABASE_URL", "postgresql+asyncpg://alos:alos@127.0.0.1:5432/alos_test"
    )
    parts = urlsplit(source)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


async def _database(name: str, *, create: bool) -> None:
    admin = await asyncpg.connect(_database_url("postgres").replace("+asyncpg", ""))
    try:
        if create:
            await admin.execute(f'CREATE DATABASE "{name}"')
        else:
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _login(
    client: httpx.AsyncClient, *, email: str, permissions: list[str],
    workspace_id: str = "workspace_property", workspace_key: str = "property",
) -> tuple[dict[str, str], str]:
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass!123",
            "display_name": "Document Test",
            "tenant_id": "tenant_default",
            "organization_id": "org_default",
            "workspace_id": workspace_id,
            "workspace_key": workspace_key,
            "workspace_name": "Document Workspace",
            "workspace_type": "BUSINESS",
            "division_code": "UNASSIGNED",
            "role_refs": ["DIVISION_MEMBER"],
            "permission_refs": permissions,
            "scope_refs": [],
            "data_scope": "WORKSPACE",
        },
    )
    assert registered.status_code == 201, registered.text
    login = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "StrongPass!123"}
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    return headers, registered.json()["actor_id"]


async def _source(
    postgres: asyncpg.Connection, *, source_id: str, workspace_id: str,
    tenant_id: str = "tenant_default", organization_id: str = "org_default",
    document_id: str | None = None,
) -> None:
    await postgres.execute(
        "INSERT INTO core.sources "
        "(source_id, tenant_id, organization_id, workspace_id, title, source_type, "
        "data_classification, document_id, created_by, created_at) "
        "VALUES ($1, $2, $3, $4, 'Authoritative source', 'PDF', 'INTERNAL', $5, "
        "'actor_source', now())",
        source_id, tenant_id, organization_id, workspace_id, document_id,
    )
    await postgres.execute(
        "INSERT INTO core.source_versions "
        "(source_id, source_version, tenant_id, organization_id, workspace_id, "
        "storage_uri, content_hash, status, created_by, created_at) "
        "VALUES ($1, '1', $2, $3, $4, $5, $6, 'VERIFIED', 'actor_source', now())",
        source_id, tenant_id, organization_id, workspace_id,
        f"urn:alos:source:{source_id}:1", "sha256:" + "a" * 64,
    )


@pytest.mark.asyncio
async def test_documents_metadata_and_immutable_versions_use_postgres() -> None:
    database_name = f"alos_document_{uuid.uuid4().hex[:12]}"
    await _database(database_name, create=True)
    database_url = _database_url(database_name)
    app = None
    try:
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=BACKEND_ROOT,
            env={**os.environ, "DATABASE_URL": database_url},
            check=True,
            capture_output=True,
            text=True,
        )
        app = create_app(
            Settings(
                _env_file=None,
                APP_ENV="test",
                DATABASE_URL=database_url,
                ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
                GENESIS_BASE_URL="http://genesis.test",
                GENESIS_INTERNAL_TOKEN="document-test-token",  # noqa: S106
            )
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for path in ("/api/v1/documents", "/api/v1/documents/missing"):
                assert (await client.get(path)).status_code == 401
            assert (await client.post("/api/v1/documents", json={})).status_code == 401
            denied, _ = await _login(client, email="document-denied@andara.local", permissions=[])
            legacy, owner_actor_id = await _login(
                client, email="document-owner@andara.local",
                permissions=["work.read", "work.write"],
            )
            versioner, versioner_actor_id = await _login(
                client, email="document-versioner@andara.local",
                permissions=["document.read", "document.version"],
            )
            other_workspace, _ = await _login(
                client, email="document-other@andara.local",
                permissions=["work.read", "work.write", "document.version"],
                workspace_id="workspace_hr", workspace_key="hr",
            )
            for path in ("/api/v1/documents", "/api/v1/documents/missing"):
                assert (await client.get(path, headers=denied)).status_code == 403
            assert (
                await client.post("/api/v1/documents", headers=denied, json={})
            ).status_code == 403
            for injected in (
                "tenant_id", "organization_id", "workspace_id", "status", "owner_actor_id"
            ):
                rejected = await client.post(
                    "/api/v1/documents", headers=legacy,
                    json={"title": "Document", "category": "Policy",
                          "data_classification": "INTERNAL", injected: "arbitrary"},
                )
                assert rejected.status_code == 422, rejected.text

            created = await client.post(
                "/api/v1/documents", headers=legacy,
                json={"title": "Document One", "category": "Policy",
                      "data_classification": "INTERNAL"},
            )
            assert created.status_code == 201, created.text
            document = created.json()
            document_id = document["document_id"]
            assert document["status"] == "DRAFT"
            assert document["tenant_id"] == "tenant_default"
            assert document["organization_id"] == "org_default"
            assert document["workspace_id"] == "workspace_property"
            assert document["owner_actor_id"] == owner_actor_id
            assert document["created_at"]
            assert "record_id" not in document
            detail = await client.get(f"/api/v1/documents/{document_id}", headers=legacy)
            assert detail.json() == document
            assert (
                await client.get(f"/api/v1/documents/{document_id}/versions", headers=legacy)
            ).json() == []
            assert (
                await client.get(f"/api/v1/documents/{document_id}", headers=other_workspace)
            ).status_code == 404
            assert (
                await client.get(
                    f"/api/v1/documents/{document_id}/versions", headers=other_workspace
                )
            ).status_code == 404
            assert (
                await client.get("/api/v1/documents?status=DRAFT", headers=legacy)
            ).json() == [document]
            for query in (
                "classification=INTERNAL", "category=Policy", "search=Document",
                "search=Policy",
            ):
                listed = await client.get(f"/api/v1/documents?{query}", headers=legacy)
                assert len(listed.json()) == 1
            for query in ("status=UNKNOWN", "classification=SECRET"):
                assert (
                    await client.get(f"/api/v1/documents?{query}", headers=legacy)
                ).status_code == 422

            remote = await client.post(
                "/api/v1/documents", headers=other_workspace,
                json={"title": "Remote document", "category": "Policy",
                      "data_classification": "INTERNAL"},
            )
            assert remote.status_code == 201, remote.text
            assert len((await client.get("/api/v1/documents", headers=legacy)).json()) == 1
            assert (
                await client.get(
                    f"/api/v1/documents/{remote.json()['document_id']}", headers=legacy
                )
            ).status_code == 404

            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                assert await postgres.fetchval(
                    "SELECT count(*) FROM core.documents WHERE document_id=$1", document_id
                ) == 1
                for suffix, tenant, organization in (
                    ("tenant", "another_tenant", "org_default"),
                    ("organization", "tenant_default", "another_organization"),
                ):
                    await postgres.execute(
                        "INSERT INTO core.documents "
                        "(document_id, tenant_id, organization_id, workspace_id, title, "
                        "category, data_classification, status, owner_actor_id, created_at) "
                        "VALUES ($1, $2, $3, 'workspace_property', 'Foreign', 'Policy', "
                        "'INTERNAL', 'DRAFT', 'actor_foreign', now())",
                        f"foreign_document_{suffix}", tenant, organization,
                    )
                await _source(postgres, source_id="source_valid", workspace_id="workspace_property")
                await _source(
                    postgres, source_id="source_second", workspace_id="workspace_property"
                )
                await _source(postgres, source_id="source_remote", workspace_id="workspace_hr")
                await _source(
                    postgres, source_id="source_version_remote",
                    workspace_id="workspace_property",
                )
                await postgres.execute(
                    "UPDATE core.source_versions SET workspace_id='workspace_hr' "
                    "WHERE source_id='source_version_remote'"
                )
                await _source(
                    postgres, source_id="source_tenant", workspace_id="workspace_property",
                    tenant_id="another_tenant",
                )
                await _source(
                    postgres, source_id="source_organization", workspace_id="workspace_property",
                    organization_id="another_organization",
                )
                await _source(
                    postgres, source_id="source_other_document", workspace_id="workspace_property",
                    document_id=remote.json()["document_id"],
                )
            finally:
                await postgres.close()

            for foreign_id in ("foreign_document_tenant", "foreign_document_organization"):
                assert (
                    await client.get(f"/api/v1/documents/{foreign_id}", headers=legacy)
                ).status_code == 404
                assert (
                    await client.get(f"/api/v1/documents/{foreign_id}/versions", headers=legacy)
                ).status_code == 404
            assert len((await client.get("/api/v1/documents", headers=legacy)).json()) == 1

            version_payload = {"version": "1.0", "source_id": "source_valid", "source_version": "1"}
            assert (
                await client.post(
                    f"/api/v1/documents/{document_id}/versions", headers=legacy,
                    json=version_payload,
                )
            ).status_code == 403
            for source_id in (
                "missing", "source_remote", "source_version_remote",
                "source_tenant", "source_organization",
                "source_other_document",
            ):
                rejected = await client.post(
                    f"/api/v1/documents/{document_id}/versions", headers=versioner,
                    json={**version_payload, "source_id": source_id},
                )
                assert rejected.status_code == 404, rejected.text
            assert (
                await client.post(
                    f"/api/v1/documents/{document_id}/versions", headers=versioner,
                    json={**version_payload, "source_version": "missing"},
                )
            ).status_code == 404
            for injected in ("storage_uri", "content_hash", "created_by", "workspace_id"):
                rejected = await client.post(
                    f"/api/v1/documents/{document_id}/versions", headers=versioner,
                    json={**version_payload, injected: "arbitrary"},
                )
                assert rejected.status_code == 422, rejected.text
            version = await client.post(
                f"/api/v1/documents/{document_id}/versions", headers=versioner,
                json=version_payload,
            )
            assert version.status_code == 201, version.text
            data = version.json()
            assert data["storage_uri"] == "urn:alos:source:source_valid:1"
            assert data["content_hash"] == "sha256:" + "a" * 64
            assert data["created_by"] == versioner_actor_id
            assert data["created_at"]
            assert (await client.get(
                f"/api/v1/documents/{document_id}/versions", headers=legacy
            )).json() == [data]
            assert (
                await client.post(
                    f"/api/v1/documents/{document_id}/versions", headers=versioner,
                    json={**version_payload, "source_id": "source_second"},
                )
            ).status_code == 409
            assert (
                await client.post(
                    f"/api/v1/documents/{document_id}/versions", headers=versioner,
                    json=version_payload,
                )
            ).status_code == 409
            assert (
                await client.patch(
                    f"/api/v1/documents/{document_id}/versions", headers=versioner,
                    json={"content_hash": "overwritten"},
                )
            ).status_code == 405
            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                persisted = await postgres.fetchrow(
                    "SELECT storage_uri, content_hash, created_by FROM core.document_versions "
                    "WHERE document_id=$1 AND version='1.0'", document_id,
                )
                assert persisted["storage_uri"] == data["storage_uri"]
                assert persisted["content_hash"] == data["content_hash"]
                assert persisted["created_by"] == data["created_by"]
                assert await postgres.fetchval(
                    "SELECT count(*) FROM core.document_versions WHERE document_id=$1", document_id
                ) == 1
            finally:
                await postgres.close()
            events = app.state.identity_audit.list_events(tenant_id="tenant_default")
            assert sum(event.event_type == "document.created" for event in events) == 2
            assert sum(event.event_type == "document.versioned" for event in events) == 1
    finally:
        if app is not None:
            await app.state.database.dispose()
        await _database(database_name, create=False)
