"""Real object, queue, source/version and document review boundary."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from test_business_domains import Context
from test_business_domains import context as migrated_context
from test_strategy_planning_e2e import _register_and_login

from alos.documents.object_store import DocumentObjectStore
from alos.jobs.models import EnqueueJob, JobType
from alos.jobs.repository import JobConflictError
from alos.jobs.sql_repository import SqlJobRepository
from alos.jobs.worker import JobWorker

pytestmark = pytest.mark.asyncio(loop_scope="module")
context = migrated_context


async def test_upload_extraction_review_and_scoped_content(
    context: Context, tmp_path: Path
) -> None:
    service = context.app.state.documents_service
    service.objects = DocumentObjectStore(tmp_path)
    response = await context.client.post(
        "/api/v1/documents",
        headers=context.headers["member"],
        json={
            "title": "Pemeriksaan biaya",
            "category": "OPERATIONS",
            "data_classification": "INTERNAL",
        },
    )
    assert response.status_code == 201, response.text
    document_id = response.json()["document_id"]
    path = f"/api/v1/documents/{document_id}/uploads"
    params = {"filename": "pemeriksaan.txt", "version": "1"}
    payload = b"Biaya pekerjaan perlu diperiksa Finance."
    for user in ("workspace", "org", "tenant"):
        denied = await context.client.post(
            path,
            headers={**context.headers[user], "Content-Type": "text/plain"},
            params=params,
            content=payload,
        )
        assert denied.status_code == 404, denied.text
    malformed = await context.client.post(
        path,
        headers={**context.headers["member"], "Content-Type": "text/plain"},
        params={**params, "filename": "../pemeriksaan.txt"},
        content=payload,
    )
    assert malformed.status_code == 422
    uploaded = await context.client.post(
        path,
        headers={**context.headers["member"], "Content-Type": "text/plain"},
        params=params,
        content=payload,
    )
    assert uploaded.status_code == 202, uploaded.text
    upload = uploaded.json()
    assert upload["status"] == "QUEUED" and upload["source_id"] is None
    repeated = await context.client.post(
        path,
        headers={**context.headers["member"], "Content-Type": "text/plain"},
        params=params,
        content=payload,
    )
    assert repeated.json()["upload_id"] == upload["upload_id"]
    changed = await context.client.post(
        path,
        headers={**context.headers["member"], "Content-Type": "text/plain"},
        params=params,
        content=b"Data berbeda",
    )
    assert changed.status_code == 409
    worker = JobWorker(
        service.jobs,
        {JobType.DOCUMENT_EXTRACTION.value: service.extract_job},
        worker_id="document-test",
    )
    result = await worker.run_once()
    assert result is not None and result.status.value == "SUCCEEDED", result
    status = await context.client.get(
        f"/api/v1/documents/uploads/{upload['upload_id']}", headers=context.headers["member"]
    )
    assert status.json()["status"] == "SUCCEEDED" and status.json()["source_id"]
    versions = await context.client.get(
        f"/api/v1/documents/{document_id}/versions", headers=context.headers["member"]
    )
    assert versions.status_code == 200, versions.text
    assert versions.json()[0]["content_hash"] == upload["content_hash"]
    assert (
        await context.client.get(
            f"/api/v1/documents/{document_id}/content", headers=context.headers["member"]
        )
    ).status_code == 409
    reviewer_headers, _actor = await _register_and_login(
        context.client, email="document-reviewer@business.test", tenant_id="tenant_business",
        organization_id="org_business", workspace_id="workspace_business", roles=["DIVISION_LEAD"],
        permissions=["document.read", "document.review", "document.approve", "work.read"],
    )
    for action in ("review", "approve"):
        reviewed = await context.client.post(
            f"/api/v1/documents/{document_id}/{action}",
            headers=reviewer_headers,
            json={"decision_reason": "Bukti telah diperiksa"},
        )
        assert reviewed.status_code == 200, reviewed.text
    content = await context.client.get(
        f"/api/v1/documents/{document_id}/content", headers=context.headers["member"]
    )
    assert content.status_code == 200, content.text
    assert (
        content.json()["content"] == payload.decode()
        and content.json()["instruction_authority"] is False
    )
    assert "storage_uri" not in content.json()
    for user in ("workspace", "org", "tenant"):
        assert (
            await context.client.get(
                f"/api/v1/documents/uploads/{upload['upload_id']}", headers=context.headers[user]
            )
        ).status_code == 404
        assert (
            await context.client.get(
                f"/api/v1/documents/{document_id}/content", headers=context.headers[user]
            )
        ).status_code == 404


async def test_sql_claim_recovery_fences_stale_workers(context: Context) -> None:
    jobs = SqlJobRepository(context.app.state.database.session_factory)
    command = EnqueueJob(
        tenant_id="tenant_business",
        organization_id="org_business",
        workspace_id="workspace_business",
        job_type=JobType.GENESIS_REQUEST,
        payload={"request": uuid4().hex},
        correlation_id="jobs-test",
        idempotency_key=uuid4().hex,
        max_attempts=2,
    )
    job = await jobs.enqueue(command)
    assert (await jobs.enqueue(command)).job_id == job.job_id
    claims = await asyncio.gather(
        *(
            jobs.claim(name, job_types=frozenset({JobType.GENESIS_REQUEST}))
            for name in ("first", "second")
        )
    )
    claimed = next(item for item in claims if item)
    assert sum(item is not None for item in claims) == 1
    recovered = await jobs.recover_stale(stale_before=datetime.now(UTC) + timedelta(seconds=1))
    assert any(item.job_id == job.job_id for item in recovered)
    replacement = await jobs.claim("replacement", job_types=frozenset({JobType.GENESIS_REQUEST}))
    assert replacement is not None and replacement.attempts == 2
    with pytest.raises(JobConflictError):
        await jobs.succeed(job.job_id, {}, worker_id=claimed.locked_by, attempt=claimed.attempts)
    completed = await jobs.succeed(job.job_id, {"safe": True}, worker_id="replacement", attempt=2)
    assert completed.status.value == "SUCCEEDED"
