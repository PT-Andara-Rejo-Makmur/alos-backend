"""An explicit human command may create a Task after fresh Backend authorization."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import insert, select

from alos.ara.models import AraRunRecord
from alos.ara.repository import AraRepository
from alos.audit import AuditEvent
from alos.domains.record_repository import RecordRepository, conflict
from alos.domains.shared_work import SharedWorkService
from alos.identity import Principal
from alos.observability.correlation import current_correlation_id
from alos.processes.authority import revalidate
from alos.security.errors import PlatformError


async def reviewed_task(
    repository: RecordRepository,
    work: SharedWorkService,
    principal: Principal,
    thread_id: str,
    run_id: str,
    proposal_id: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    if not principal.permissions & {"task.create", "work.write"}:
        raise PlatformError(
            "ARA_ACTION_DENIED", "Kewenangan membuat tugas diperlukan.", status_code=403
        )
    digest = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    async with repository.factory() as session, session.begin():
        await revalidate(repository, session, principal)
        await AraRepository(repository.factory)._thread(session, principal, thread_id, lock=True)
        run = await session.scalar(
            select(AraRunRecord)
            .where(AraRunRecord.run_id == run_id, AraRunRecord.thread_id == thread_id)
            .with_for_update()
        )
        if run is None:
            raise PlatformError("ARA_RUN_NOT_FOUND", "Run tidak tersedia.", status_code=404)
        proposal = (run.response or {}).get("action_proposal", {})
        if run.status != "COMPLETED" or proposal.get("proposal_id") != proposal_id:
            raise conflict("Usulan tidak berasal dari jawaban yang telah selesai.")
        if (
            proposal.get("kind") != "TASK"
            or proposal.get("status") != "NEEDS_REVIEW"
            or proposal.get("executed") is not False
        ):
            raise conflict("Usulan ini membutuhkan perintah bisnis pada domain pemiliknya.")
        if repository.contracts is None:
            raise conflict("Kontrak canonical belum tersedia.")
        repository.contracts.validate(
            "https://schemas.alos.dev/v1/ara/ara-action-proposal-projection.schema.json", proposal
        )
        table = await repository.table(session, "core", "ara_proposal_executions")
        old = (
            (
                await session.execute(
                    select(table).where(
                        *repository.scope(table, principal),
                        table.c.actor_id == principal.actor_id,
                        table.c.proposal_id == proposal_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if old:
            if old["command_hash"] != digest or old["run_id"] != run_id:
                raise conflict("Usulan sudah dijalankan dengan perintah yang berbeda.")
            return dict(jsonable_encoder(dict(old)))
        # Only the human-reviewed canonical command supplies task fields. The LLM
        # proposal is never interpreted as a payload, permission or assignment.
        command = repository.contracts.validate(
            "https://schemas.alos.dev/v1/shared-work/shared-work.schema.json#/$defs/TaskCreateRequest",
            values["task"],
        )
        task = await work.create_task_in_session(session, principal, command)
        receipt = {
            "execution_id": uuid4().hex,
            "tenant_id": principal.tenant_id,
            "organization_id": principal.organization_id,
            "workspace_id": principal.workspace_id,
            "actor_id": principal.actor_id,
            "run_id": run_id,
            "proposal_id": proposal_id,
            "task_id": task["task_id"],
            "command_hash": digest,
            "review_reason": values["review_reason"],
            "correlation_id": current_correlation_id(),
            "executed_at": datetime.now(UTC),
        }
        await session.execute(insert(table).values(**receipt))
        await repository.audit.append_in_session(
            session,
            AuditEvent(
                event_type="ara.proposal.task_executed",
                entity_type="task",
                entity_id=task["task_id"],
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=receipt["correlation_id"],
                outcome="SUCCEEDED",
                occurred_at=receipt["executed_at"],
                reason=values["review_reason"],
                metadata={
                    "thread_id": thread_id,
                    "run_id": run_id,
                    "proposal_id": proposal_id,
                    "command_hash": digest,
                    "roles": sorted(principal.roles),
                    "permissions": sorted(principal.permissions),
                },
            ),
        )
        return dict(jsonable_encoder(receipt))


async def execution_receipt(
    repository: RecordRepository,
    principal: Principal,
    thread_id: str,
    run_id: str,
    proposal_id: str,
) -> dict[str, Any]:
    async with repository.factory() as session, session.begin():
        await revalidate(repository, session, principal)
        await AraRepository(repository.factory)._thread(session, principal, thread_id)
        table = await repository.table(session, "core", "ara_proposal_executions")
        receipt = (
            (
                await session.execute(
                    select(table).where(
                        *repository.scope(table, principal),
                        table.c.actor_id == principal.actor_id,
                        table.c.run_id == run_id,
                        table.c.proposal_id == proposal_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if receipt is None:
            raise PlatformError("ARA_EXECUTION_NOT_FOUND", "Tugas belum dibuat.", status_code=404)
        return dict(jsonable_encoder(dict(receipt)))
