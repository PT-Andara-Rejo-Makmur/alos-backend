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
    status: str = "VERIFIED",
    source_version: str = "1",
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
        "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'actor_source', now())",
        source_id, source_version, tenant_id, organization_id, workspace_id,
        f"urn:alos:source:{source_id}:{source_version}", "sha256:" + "a" * 64, status,
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
                await _source(
                    postgres, source_id="source_received", workspace_id="workspace_property",
                    status="RECEIVED",
                )
                await _source(
                    postgres, source_id="source_retired", workspace_id="workspace_property",
                    status="RETIRED",
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
                "source_received", "source_retired",
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

            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                assert await postgres.fetchval(
                    "SELECT count(*) FROM core.document_versions WHERE document_id=$1", document_id
                ) == 0
            finally:
                await postgres.close()

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


@pytest.mark.asyncio
async def test_document_version_requires_verified_source_version() -> None:
    database_name = f"alos_doc_ver_{uuid.uuid4().hex[:12]}"
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
            writer, writer_actor_id = await _login(
                client,
                email="doc-writer@andara.local",
                permissions=["document.read", "document.version", "work.write"],
            )
            created = await client.post(
                "/api/v1/documents",
                headers=writer,
                json={
                    "title": "Verified Provenance Doc",
                    "category": "Policy",
                    "data_classification": "INTERNAL",
                },
            )
            assert created.status_code == 201, created.text
            document_id = created.json()["document_id"]

            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                # 1. Source with RECEIVED status
                await _source(
                    postgres,
                    source_id="source_received",
                    workspace_id="workspace_property",
                    status="RECEIVED",
                    source_version="v-rec",
                )
                # 2. Source with RETIRED status
                await _source(
                    postgres,
                    source_id="source_retired",
                    workspace_id="workspace_property",
                    status="RETIRED",
                    source_version="v-ret",
                )
                # 3. Source with VERIFIED status
                await _source(
                    postgres,
                    source_id="source_verified",
                    workspace_id="workspace_property",
                    status="VERIFIED",
                    source_version="v-ver",
                )
                assert await postgres.fetchval(
                    "SELECT count(*) FROM core.document_versions WHERE document_id=$1",
                    document_id,
                ) == 0
            finally:
                await postgres.close()

            # Attempt creation from RECEIVED SourceVersion -> rejected (404)
            resp_received = await client.post(
                f"/api/v1/documents/{document_id}/versions",
                headers=writer,
                json={"version": "1.0", "source_id": "source_received", "source_version": "v-rec"},
            )
            assert resp_received.status_code == 404, resp_received.text

            # Verify no rows created in core.document_versions after RECEIVED rejection
            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                assert await postgres.fetchval(
                    "SELECT count(*) FROM core.document_versions WHERE document_id=$1",
                    document_id,
                ) == 0
            finally:
                await postgres.close()

            # Attempt creation from RETIRED SourceVersion -> rejected (404)
            resp_retired = await client.post(
                f"/api/v1/documents/{document_id}/versions",
                headers=writer,
                json={"version": "1.0", "source_id": "source_retired", "source_version": "v-ret"},
            )
            assert resp_retired.status_code == 404, resp_retired.text

            # Verify no rows created in core.document_versions after RETIRED rejection
            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                assert await postgres.fetchval(
                    "SELECT count(*) FROM core.document_versions WHERE document_id=$1",
                    document_id,
                ) == 0
            finally:
                await postgres.close()

            # Attempt creation from VERIFIED SourceVersion -> succeeded (201)
            resp_verified = await client.post(
                f"/api/v1/documents/{document_id}/versions",
                headers=writer,
                json={"version": "1.0", "source_id": "source_verified", "source_version": "v-ver"},
            )
            assert resp_verified.status_code == 201, resp_verified.text
            version_data = resp_verified.json()
            assert version_data["storage_uri"] == "urn:alos:source:source_verified:v-ver"
            assert version_data["content_hash"] == "sha256:" + "a" * 64
            assert version_data["created_by"] == writer_actor_id

            # Verify authoritative row in core.document_versions
            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                assert await postgres.fetchval(
                    "SELECT count(*) FROM core.document_versions WHERE document_id=$1",
                    document_id,
                ) == 1
                row = await postgres.fetchrow(
                    "SELECT storage_uri, content_hash, created_by FROM core.document_versions "
                    "WHERE document_id=$1 AND version='1.0'",
                    document_id,
                )
                assert row["storage_uri"] == "urn:alos:source:source_verified:v-ver"
                assert row["content_hash"] == "sha256:" + "a" * 64
                assert row["created_by"] == writer_actor_id
            finally:
                await postgres.close()

            # Immutability: cannot overwrite or modify
            conflict = await client.post(
                f"/api/v1/documents/{document_id}/versions",
                headers=writer,
                json={"version": "1.0", "source_id": "source_verified", "source_version": "v-ver"},
            )
            assert conflict.status_code == 409
            method_not_allowed = await client.patch(
                f"/api/v1/documents/{document_id}/versions",
                headers=writer,
                json={"content_hash": "overwritten"},
            )
            assert method_not_allowed.status_code == 405

            # Immutability: verify count is still 1 and row unchanged
            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                assert await postgres.fetchval(
                    "SELECT count(*) FROM core.document_versions WHERE document_id=$1",
                    document_id,
                ) == 1
                row = await postgres.fetchrow(
                    "SELECT storage_uri, content_hash, created_by FROM core.document_versions "
                    "WHERE document_id=$1 AND version='1.0'",
                    document_id,
                )
                assert row["storage_uri"] == "urn:alos:source:source_verified:v-ver"
                assert row["content_hash"] == "sha256:" + "a" * 64
                assert row["created_by"] == writer_actor_id
            finally:
                await postgres.close()
    finally:
        if app is not None:
            await app.state.database.dispose()
        await _database(database_name, create=False)


@pytest.mark.asyncio
async def test_document_lifecycle_canonical_transitions_and_rules() -> None:
    database_name = f"alos_doc_lc_{uuid.uuid4().hex[:12]}"
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
            owner, owner_actor_id = await _login(
                client,
                email="doc-owner@andara.local",
                permissions=[
                    "document.read", "document.create", "document.version",
                    "document.review", "document.approve", "document.retire",
                ],
            )
            legacy_writer, _ = await _login(
                client,
                email="doc-legacy-writer@andara.local",
                permissions=["work.read", "work.write"],
            )
            reviewer, _ = await _login(
                client,
                email="doc-reviewer@andara.local",
                permissions=["document.read", "document.review"],
            )
            approver, _ = await _login(
                client,
                email="doc-approver@andara.local",
                permissions=["document.read", "document.approve"],
            )
            retirer, _ = await _login(
                client,
                email="doc-retirer@andara.local",
                permissions=["document.read", "document.retire"],
            )
            other_workspace, _ = await _login(
                client,
                email="doc-other-ws@andara.local",
                permissions=[
                    "document.read", "document.review", "document.approve", "document.retire",
                ],
                workspace_id="workspace_hr",
                workspace_key="hr",
            )

            # 1. Create document (starts in DRAFT)
            created = await client.post(
                "/api/v1/documents",
                headers=owner,
                json={
                    "title": "Lifecycle Policy Document",
                    "category": "Policy",
                    "data_classification": "INTERNAL",
                },
            )
            assert created.status_code == 201, created.text
            doc_id = created.json()["document_id"]
            assert created.json()["status"] == "DRAFT"
            assert created.json()["owner_actor_id"] == owner_actor_id

            # 2. Authorization checks: work.write cannot execute lifecycle
            for action in ("review", "approve", "retire"):
                denied_legacy = await client.post(
                    f"/api/v1/documents/{doc_id}/{action}",
                    headers=legacy_writer,
                )
                assert denied_legacy.status_code == 403

            # document.review cannot approve or retire
            assert (
                await client.post(f"/api/v1/documents/{doc_id}/approve", headers=reviewer)
            ).status_code == 403
            assert (
                await client.post(f"/api/v1/documents/{doc_id}/retire", headers=reviewer)
            ).status_code == 403

            # document.approve cannot retire
            assert (
                await client.post(f"/api/v1/documents/{doc_id}/retire", headers=approver)
            ).status_code == 403

            # Cross-workspace isolation
            assert (
                await client.post(f"/api/v1/documents/{doc_id}/review", headers=other_workspace)
            ).status_code == 404

            # 3. DRAFT without version cannot enter review
            no_version_review = await client.post(
                f"/api/v1/documents/{doc_id}/review",
                headers=reviewer,
            )
            assert no_version_review.status_code == 409
            assert "DOCUMENT_VERSION_REQUIRED" in no_version_review.text

            # 4. Add authoritative VERIFIED source and version while in DRAFT
            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                await _source(
                    postgres,
                    source_id="src_lifecycle_01",
                    workspace_id="workspace_property",
                    status="VERIFIED",
                    source_version="v1",
                )
            finally:
                await postgres.close()

            version_res = await client.post(
                f"/api/v1/documents/{doc_id}/versions",
                headers=owner,
                json={"version": "1.0", "source_id": "src_lifecycle_01", "source_version": "v1"},
            )
            assert version_res.status_code == 201, version_res.text

            # 5. Review transition: DRAFT -> IN_REVIEW
            review_res = await client.post(
                f"/api/v1/documents/{doc_id}/review",
                headers=reviewer,
            )
            assert review_res.status_code == 200, review_res.text
            assert review_res.json()["status"] == "IN_REVIEW"

            # Idempotent review: does not raise error and produces no duplicate audit event
            review_idempotent = await client.post(
                f"/api/v1/documents/{doc_id}/review",
                headers=reviewer,
            )
            assert review_idempotent.status_code == 200
            assert review_idempotent.json()["status"] == "IN_REVIEW"

            events = app.state.identity_audit.list_events(tenant_id="tenant_default")
            assert sum(event.event_type == "document.reviewed" for event in events) == 1

            # 6. Version freeze: cannot add version when IN_REVIEW
            frozen_in_review = await client.post(
                f"/api/v1/documents/{doc_id}/versions",
                headers=owner,
                json={"version": "2.0", "source_id": "src_lifecycle_01", "source_version": "v1"},
            )
            assert frozen_in_review.status_code == 409
            assert "DOCUMENT_VERSION_FROZEN" in frozen_in_review.text

            # 7. Invalid transition from IN_REVIEW: cannot retire directly
            invalid_retire = await client.post(
                f"/api/v1/documents/{doc_id}/retire",
                headers=retirer,
            )
            assert invalid_retire.status_code == 409
            assert "DOCUMENT_STATUS_CONFLICT" in invalid_retire.text

            # 8. Separation of Duties: Document owner CANNOT self-approve
            owner_self_approve = await client.post(
                f"/api/v1/documents/{doc_id}/approve",
                headers=owner,
            )
            assert owner_self_approve.status_code == 403
            assert "DOCUMENT_SELF_APPROVAL_DENIED" in owner_self_approve.text

            # Verify status still IN_REVIEW
            owner_check = await client.get(f"/api/v1/documents/{doc_id}", headers=owner)
            assert owner_check.json()["status"] == "IN_REVIEW"

            # 9. Valid approve by different actor with document.approve
            approve_res = await client.post(
                f"/api/v1/documents/{doc_id}/approve",
                headers=approver,
            )
            assert approve_res.status_code == 200, approve_res.text
            assert approve_res.json()["status"] == "APPROVED"

            # Idempotent approve by same or eligible approver
            approve_idempotent = await client.post(
                f"/api/v1/documents/{doc_id}/approve",
                headers=approver,
            )
            assert approve_idempotent.status_code == 200
            assert approve_idempotent.json()["status"] == "APPROVED"

            events = app.state.identity_audit.list_events(tenant_id="tenant_default")
            assert sum(event.event_type == "document.approved" for event in events) == 1

            # Owner still cannot approve even after approved
            assert (
                await client.post(f"/api/v1/documents/{doc_id}/approve", headers=owner)
            ).status_code == 403

            # 10. Version freeze: cannot add version when APPROVED
            frozen_approved = await client.post(
                f"/api/v1/documents/{doc_id}/versions",
                headers=owner,
                json={"version": "2.0", "source_id": "src_lifecycle_01", "source_version": "v1"},
            )
            assert frozen_approved.status_code == 409
            assert "DOCUMENT_VERSION_FROZEN" in frozen_approved.text

            # 11. Invalid transition from APPROVED: cannot re-review
            invalid_review = await client.post(
                f"/api/v1/documents/{doc_id}/review",
                headers=reviewer,
            )
            assert invalid_review.status_code == 409

            # 12. Valid retire: APPROVED -> RETIRED
            retire_res = await client.post(
                f"/api/v1/documents/{doc_id}/retire",
                headers=retirer,
            )
            assert retire_res.status_code == 200, retire_res.text
            assert retire_res.json()["status"] == "RETIRED"

            # Idempotent retire
            retire_idempotent = await client.post(
                f"/api/v1/documents/{doc_id}/retire",
                headers=retirer,
            )
            assert retire_idempotent.status_code == 200
            assert retire_idempotent.json()["status"] == "RETIRED"

            events = app.state.identity_audit.list_events(tenant_id="tenant_default")
            assert sum(event.event_type == "document.retired" for event in events) == 1

            # 13. Version freeze: cannot add version when RETIRED
            frozen_retired = await client.post(
                f"/api/v1/documents/{doc_id}/versions",
                headers=owner,
                json={"version": "2.0", "source_id": "src_lifecycle_01", "source_version": "v1"},
            )
            assert frozen_retired.status_code == 409
            assert "DOCUMENT_VERSION_FROZEN" in frozen_retired.text

            # 14. Invalid transitions from RETIRED (RETIRED is final)
            assert (
                await client.post(f"/api/v1/documents/{doc_id}/review", headers=reviewer)
            ).status_code == 409
            assert (
                await client.post(f"/api/v1/documents/{doc_id}/approve", headers=approver)
            ).status_code == 409

            # 15. Verify persistence in PostgreSQL: exactly 1 version row and final status RETIRED
            postgres = await asyncpg.connect(database_url.replace("+asyncpg", ""))
            try:
                doc_row = await postgres.fetchrow(
                    "SELECT status, owner_actor_id FROM core.documents WHERE document_id=$1",
                    doc_id,
                )
                assert doc_row["status"] == "RETIRED"
                assert doc_row["owner_actor_id"] == owner_actor_id
                assert await postgres.fetchval(
                    "SELECT count(*) FROM core.document_versions WHERE document_id=$1",
                    doc_id,
                ) == 1
            finally:
                await postgres.close()
    finally:
        if app is not None:
            await app.state.database.dispose()
        await _database(database_name, create=False)


