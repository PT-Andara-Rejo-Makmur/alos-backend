"""Actual Backend/GENESIS runtime and PostgreSQL authority; no live model claim."""

import sys
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, update
from test_business_domains import Context
from test_business_domains import context as migrated_context
from test_business_processes import change_order, configure, submit

from alos.dependencies import get_genesis_client
from alos.integrations.genesis import GenesisClient
from alos.persistence.models import AuditRecord

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "genesis-ai" / "src"))
from genesis.config import Settings as GenesisSettings
from genesis.main import create_app as genesis_app

context = migrated_context
pytestmark = pytest.mark.asyncio(loop_scope="module")


async def test_question_progress_sources_reviewed_task_and_current_authority(
    context: Context,
) -> None:
    app = context.app
    app.state.settings.ENABLE_TEST_TOOLS = True
    token = SecretStr("business-runtime-test-token")
    app.state.settings.GENESIS_INTERNAL_TOKEN = token
    runtime = genesis_app(
        GenesisSettings(
            _env_file=None,
            APP_ENV="test",
            ALOS_BACKEND_BASE_URL="http://backend.test",
            ALOS_INTERNAL_TOKEN=token,
            ALOS_CONTRACTS_PATH=app.state.settings.ALOS_CONTRACTS_PATH,
            ENABLE_TEST_RUNTIME=True,
        )
    )
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://backend.test"
        ) as backend,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime), base_url="http://genesis.test"
        ) as intelligence,
    ):
        runtime.state.backend_http_client = backend
        app.dependency_overrides[get_genesis_client] = lambda: GenesisClient(
            base_url="http://genesis.test", internal_token=token, client=intelligence
        )
        customer = await context.create(
            "sales", "customers", {"customer_code": "ARA", "name": "Informasi berizin"}
        )
        await context.create(
            "sales",
            "leads",
            {"customer_id": customer["customer_id"], "source": "Canonical", "interest": "Unit"},
        )
        created = await context.client.post(
            "/api/v1/ara/threads", headers=context.headers["member"], json={}
        )
        assert created.status_code == 201, created.text
        thread = created.json()["thread_id"]
        answer = await context.client.post(
            f"/api/v1/ara/threads/{thread}/messages",
            headers=context.headers["member"],
            json={"message": "Tampilkan lead Sales"},
        )
        assert answer.status_code == 200, answer.text
        body = answer.json()
        assert body["status"] == "COMPLETED", body
        sources = body["response"]["sources"]
        assert sources and sources[0]["tool_id"] == "sales.lead.list"
        assert sources[0]["evidence_ref"]["instruction_authority"] is False
        progress = await context.client.get(
            f"/api/v1/ara/threads/{thread}/runs/{body['run_id']}/progress",
            headers=context.headers["member"],
        )
        kinds = {event["kind"] for event in progress.json()["events"]}
        assert {"UNDERSTANDING", "RETRIEVING", "ANALYZING", "PREPARING", "COMPLETED"} <= kinds
        assert "reasoning" not in progress.text
        await configure(context, "CHANGE_ORDER")
        change = await change_order(context)
        process = await submit(context, "CHANGE_ORDER", change["change_order_id"])
        foreign_thread = await context.client.post(
            "/api/v1/ara/threads", headers=context.headers["workspace"], json={}
        )
        assert foreign_thread.status_code == 201, foreign_thread.text
        process_answer = await context.client.post(
            f"/api/v1/ara/threads/{foreign_thread.json()['thread_id']}/messages",
            headers=context.headers["workspace"],
            json={
                "message": "Jelaskan pengajuan dan pemeriksaan yang diperlukan",
                "business_reference": {"domain": "PROCESS", "resource_id": process["process_id"]},
            },
        )
        assert process_answer.status_code == 200, process_answer.text
        process_sources = process_answer.json()["response"]["sources"]
        assert process_sources and {source["tool_id"] for source in process_sources} == {
            "process.detail.read"
        }
        hidden_record = await context.client.get(
            f"/api/v1/property/change-orders/{change['change_order_id']}",
            headers=context.headers["workspace"],
        )
        assert hidden_record.status_code == 404
        proposal_run = await context.client.post(
            f"/api/v1/ara/threads/{thread}/messages",
            headers=context.headers["member"],
            json={"message": "Buat tugas tindak lanjut"},
        )
        assert proposal_run.status_code == 200, proposal_run.text
        run = proposal_run.json()
        proposal = run["response"]["action_proposal"]
        assert proposal["kind"] == "TASK" and proposal["executed"] is False
        path = (
            f"/api/v1/ara/threads/{thread}/runs/{run['run_id']}"
            f"/proposals/{proposal['proposal_id']}/task"
        )
        command = {
            "review_reason": "Kebutuhan dan rincian diperiksa manusia",
            "task": {"title": "Hubungi calon pelanggan", "priority": "NORMAL"},
        }
        for user in ("workspace", "org", "tenant"):
            hidden = await context.client.post(path, headers=context.headers[user], json=command)
            assert hidden.status_code == 404, hidden.text
        result = await context.client.post(path, headers=context.headers["member"], json=command)
        assert result.status_code == 201, result.text
        receipt = result.json()
        repeated = await context.client.post(path, headers=context.headers["member"], json=command)
        assert repeated.json()["task_id"] == receipt["task_id"]
        altered = await context.client.post(
            path,
            headers=context.headers["member"],
            json={**command, "review_reason": "Perintah berbeda"},
        )
        assert altered.status_code == 409
        refreshed = await context.client.get(path, headers=context.headers["member"])
        assert refreshed.json() == receipt
        original = await context.client.get(
            f"/api/v1/ara/threads/{thread}/runs/{run['run_id']}", headers=context.headers["member"]
        )
        assert original.json()["response"]["action_proposal"] == proposal
        repository = app.state.process_service.repository
        async with repository.factory() as session, session.begin():
            members = await repository.table(session, "core", "workspace_memberships")
            row = (
                (
                    await session.execute(
                        select(members).where(
                            members.c.actor_id == receipt["actor_id"],
                            members.c.workspace_id == receipt["workspace_id"],
                        )
                    )
                )
                .mappings()
                .one()
            )
            await session.execute(
                update(members)
                .where(
                    members.c.actor_id == receipt["actor_id"],
                    members.c.workspace_id == receipt["workspace_id"],
                )
                .values(
                    permission_refs=[
                        p for p in row["permission_refs"] if p not in {"task.create", "work.write"}
                    ]
                )
            )
        stale = await context.client.post(path, headers=context.headers["member"], json=command)
        assert stale.status_code == 403, stale.text
        async with repository.factory() as session:
            executions = await repository.table(session, "core", "ara_proposal_executions")
            assert await session.scalar(select(func.count()).select_from(executions)) == 1
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AuditRecord)
                    .where(AuditRecord.event_type == "ara.proposal.task_executed")
                )
                == 1
            )
        app.dependency_overrides.pop(get_genesis_client)
