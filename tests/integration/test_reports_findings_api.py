"""Integration tests for authoritative Reports and Findings API."""

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
    client: httpx.AsyncClient,
    *,
    email: str,
    permissions: list[str],
    tenant_id: str = "tenant_default",
    organization_id: str = "org_default",
    workspace_id: str = "workspace_property",
    workspace_key: str = "property",
) -> tuple[dict[str, str], str]:
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "StrongPass!123",
            "display_name": "Report Finding Test",
            "tenant_id": tenant_id,
            "organization_id": organization_id,
            "workspace_id": workspace_id,
            "workspace_key": workspace_key,
            "workspace_name": "Test Workspace",
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


@pytest.mark.asyncio
async def test_reports_and_findings_authoritative_endpoints_and_generic_guards() -> None:
    database_name = f"alos_rf_{uuid.uuid4().hex[:8]}"
    await _database(database_name, create=True)
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(BACKEND_ROOT),
        env={**os.environ, "DATABASE_URL": _database_url(database_name)},
        check=True,
    )
    app = None
    try:
        settings = Settings(
            _env_file=None,
            APP_ENV="test",
            DATABASE_URL=_database_url(database_name),
            ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
            ENABLE_TEST_REGISTRATION=True,
        )
        app = create_app(settings)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Actors setup
            report_creator, creator_actor_id = await _login(
                client,
                email="rf-creator@andara.local",
                permissions=["report.create", "report.read", "finding.create", "finding.read"],
            )
            legacy_writer, _ = await _login(
                client,
                email="rf-legacy@andara.local",
                permissions=["work.write", "work.read", "work.delete"],
            )
            read_only_user, _ = await _login(
                client,
                email="rf-readonly@andara.local",
                permissions=["report.read", "finding.read"],
            )
            no_perms_user, _ = await _login(
                client,
                email="rf-noperms@andara.local",
                permissions=[],
            )
            other_ws_user, _ = await _login(
                client,
                email="rf-otherws@andara.local",
                permissions=["report.read", "report.create", "finding.read", "finding.create"],
                workspace_id="workspace_finance",
                workspace_key="finance",
            )
            other_org_user, _ = await _login(
                client,
                email="rf-otherorg@andara.local",
                permissions=["report.read", "report.create", "finding.read", "finding.create"],
                organization_id="org_second",
                workspace_id="workspace_second_org",
                workspace_key="second_org",
            )
            other_tenant_user, _ = await _login(
                client,
                email="rf-othertenant@other.local",
                permissions=["report.read", "report.create", "finding.read", "finding.create"],
                tenant_id="tenant_other",
                organization_id="org_other",
                workspace_id="workspace_other_tenant",
                workspace_key="other_tenant",
            )

            # --- REPORTS TESTS ---

            # Unauthenticated -> 401
            assert (await client.get("/api/v1/work/reports/results")).status_code == 401
            assert (
                await client.post(
                    "/api/v1/work/reports/results",
                    json={"title": "Test", "report_type": "FINANCIAL"},
                )
            ).status_code == 401

            # Insufficient permission -> 403
            assert (
                await client.get("/api/v1/work/reports/results", headers=no_perms_user)
            ).status_code == 403
            assert (
                await client.post(
                    "/api/v1/work/reports/results",
                    headers=read_only_user,
                    json={"title": "Test", "report_type": "FINANCIAL"},
                )
            ).status_code == 403

            # Authority injection -> 422
            injected_report = await client.post(
                "/api/v1/work/reports/results",
                headers=report_creator,
                json={
                    "title": "Injected Report",
                    "report_type": "FINANCIAL",
                    "status": "APPROVED",
                    "owner_actor_id": "fake_actor",
                    "tenant_id": "fake_tenant",
                },
            )
            assert injected_report.status_code == 422

            # Valid report creation via report.create
            create_report_res = await client.post(
                "/api/v1/work/reports/results",
                headers=report_creator,
                json={"title": "Laporan Triwulan I", "report_type": "FINANCIAL"},
            )
            assert create_report_res.status_code == 201, create_report_res.text
            report = create_report_res.json()
            assert report["title"] == "Laporan Triwulan I"
            assert report["report_type"] == "FINANCIAL"
            assert report["status"] == "DRAFT"
            assert report["owner_actor_id"] == creator_actor_id
            assert report["tenant_id"] == "tenant_default"
            assert report["organization_id"] == "org_default"
            assert report["workspace_ids"] == ["workspace_property"]
            report_id = report["report_id"]

            # Report creation via fallback work.write
            legacy_report_res = await client.post(
                "/api/v1/work/reports/results",
                headers=legacy_writer,
                json={"title": "Laporan Operasional", "report_type": "OPERATIONAL"},
            )
            assert legacy_report_res.status_code == 201
            assert legacy_report_res.json()["status"] == "DRAFT"

            # Get report detail
            get_report_res = await client.get(
                f"/api/v1/work/reports/results/{report_id}",
                headers=report_creator,
            )
            assert get_report_res.status_code == 200
            assert get_report_res.json() == report

            # Read report detail with legacy work.read
            get_legacy_read = await client.get(
                f"/api/v1/work/reports/results/{report_id}",
                headers=legacy_writer,
            )
            assert get_legacy_read.status_code == 200

            # Cross-scope isolation: workspace, org, tenant -> 404 on detail, invisible in list
            for unauthorized_user in (other_ws_user, other_org_user, other_tenant_user):
                res_detail = await client.get(
                    f"/api/v1/work/reports/results/{report_id}",
                    headers=unauthorized_user,
                )
                assert res_detail.status_code == 404

                res_list = await client.get(
                    "/api/v1/work/reports/results",
                    headers=unauthorized_user,
                )
                assert res_list.status_code == 200
                assert not any(r["report_id"] == report_id for r in res_list.json())

            # List reports with filters
            list_all = await client.get(
                "/api/v1/work/reports/results",
                headers=report_creator,
            )
            assert list_all.status_code == 200
            assert len(list_all.json()) >= 2

            list_draft = await client.get(
                "/api/v1/work/reports/results?status=DRAFT",
                headers=report_creator,
            )
            assert list_draft.status_code == 200
            assert all(r["status"] == "DRAFT" for r in list_draft.json())

            list_search = await client.get(
                "/api/v1/work/reports/results?search=Triwulan",
                headers=report_creator,
            )
            assert list_search.status_code == 200
            assert any(r["report_id"] == report_id for r in list_search.json())

            # Invalid status filter -> 422
            invalid_filter = await client.get(
                "/api/v1/work/reports/results?status=UNKNOWN_STATUS",
                headers=report_creator,
            )
            assert invalid_filter.status_code == 422

            # --- FINDINGS TESTS ---

            # Unauthenticated -> 401
            assert (await client.get("/api/v1/work/findings")).status_code == 401
            assert (
                await client.post(
                    "/api/v1/work/findings",
                    json={"title": "Test Finding"},
                )
            ).status_code == 401

            # Insufficient permission -> 403
            assert (
                await client.get("/api/v1/work/findings", headers=no_perms_user)
            ).status_code == 403
            assert (
                await client.post(
                    "/api/v1/work/findings",
                    headers=read_only_user,
                    json={"title": "Test Finding"},
                )
            ).status_code == 403

            # Authority injection -> 422
            injected_finding = await client.post(
                "/api/v1/work/findings",
                headers=report_creator,
                json={
                    "title": "Injected Finding",
                    "status": "CLOSED",
                    "source_type": "AUTOMATED",
                    "owner_actor_id": "fake_actor",
                },
            )
            assert injected_finding.status_code == 422

            # Valid finding creation via finding.create
            create_finding_res = await client.post(
                "/api/v1/work/findings",
                headers=report_creator,
                json={
                    "title": "Kerusakan Dinding Pembatas",
                    "description": "Retak struktural terdeteksi di sisi barat",
                    "severity": "HIGH",
                },
            )
            assert create_finding_res.status_code == 201, create_finding_res.text
            finding = create_finding_res.json()
            assert finding["title"] == "Kerusakan Dinding Pembatas"
            assert finding["description"] == "Retak struktural terdeteksi di sisi barat"
            assert finding["severity"] == "HIGH"
            assert finding["status"] == "OPEN"
            assert finding["source_type"] == "MANUAL"
            assert finding["owner_actor_id"] == creator_actor_id
            assert finding["workspace_ids"] == ["workspace_property"]
            finding_id = finding["finding_id"]

            # Finding creation via fallback work.write with default severity
            legacy_finding_res = await client.post(
                "/api/v1/work/findings",
                headers=legacy_writer,
                json={"title": "Temuan Kebersihan"},
            )
            assert legacy_finding_res.status_code == 201
            assert legacy_finding_res.json()["severity"] == "MEDIUM"
            assert legacy_finding_res.json()["status"] == "OPEN"
            assert legacy_finding_res.json()["source_type"] == "MANUAL"

            # Get finding detail
            get_finding_res = await client.get(
                f"/api/v1/work/findings/{finding_id}",
                headers=report_creator,
            )
            assert get_finding_res.status_code == 200
            assert get_finding_res.json() == finding

            # Cross-scope isolation: workspace, org, tenant -> 404 on detail, invisible in list
            for unauthorized_user in (other_ws_user, other_org_user, other_tenant_user):
                res_detail = await client.get(
                    f"/api/v1/work/findings/{finding_id}",
                    headers=unauthorized_user,
                )
                assert res_detail.status_code == 404

                res_list = await client.get(
                    "/api/v1/work/findings",
                    headers=unauthorized_user,
                )
                assert res_list.status_code == 200
                assert not any(f["finding_id"] == finding_id for f in res_list.json())

            # List findings with filters
            list_findings_res = await client.get(
                "/api/v1/work/findings",
                headers=report_creator,
            )
            assert list_findings_res.status_code == 200
            assert len(list_findings_res.json()) >= 2

            list_high_res = await client.get(
                "/api/v1/work/findings?severity=HIGH",
                headers=report_creator,
            )
            assert list_high_res.status_code == 200
            assert any(f["finding_id"] == finding_id for f in list_high_res.json())

            # Invalid status / severity filter -> 422
            assert (
                await client.get("/api/v1/work/findings?status=INVALID", headers=report_creator)
            ).status_code == 422
            assert (
                await client.get(
                    "/api/v1/work/findings?severity=SUPER_HIGH", headers=report_creator
                )
            ).status_code == 422

            # --- GENERIC CRUD CLOSURE GUARDS ---
            # Attempt generic CRUD mutation on work_reports using work.write
            assert (
                await client.post(
                    "/api/v1/domains/shared/work_reports",
                    headers=legacy_writer,
                    json={"title": "Generic Report", "report_type": "TEST"},
                )
            ).status_code == 409

            assert (
                await client.patch(
                    f"/api/v1/domains/shared/work_reports/{report_id}",
                    headers=legacy_writer,
                    json={"title": "Hacked Title"},
                )
            ).status_code == 409

            assert (
                await client.delete(
                    f"/api/v1/domains/shared/work_reports/{report_id}",
                    headers=legacy_writer,
                )
            ).status_code == 409

            # Attempt generic CRUD mutation on work_findings using work.write
            assert (
                await client.post(
                    "/api/v1/domains/shared/work_findings",
                    headers=legacy_writer,
                    json={"title": "Generic Finding"},
                )
            ).status_code == 409

            assert (
                await client.patch(
                    f"/api/v1/domains/shared/work_findings/{finding_id}",
                    headers=legacy_writer,
                    json={"title": "Hacked Finding"},
                )
            ).status_code == 409

            assert (
                await client.delete(
                    f"/api/v1/domains/shared/work_findings/{finding_id}",
                    headers=legacy_writer,
                )
            ).status_code == 409

            # --- AUDIT ASSERTION ---
            events = app.state.identity_audit.list_events(tenant_id="tenant_default")
            # Exact 1 report.created event for our first report
            report_events = [
                e for e in events
                if e.event_type == "report.created" and e.entity_id == report_id
            ]
            assert len(report_events) == 1

            # Exact 1 finding.created event for our first finding
            finding_events = [
                e for e in events
                if e.event_type == "finding.created" and e.entity_id == finding_id
            ]
            assert len(finding_events) == 1

    finally:
        if app is not None:
            await app.state.database.dispose()
        await _database(database_name, create=False)
