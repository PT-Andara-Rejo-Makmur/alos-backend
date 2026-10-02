"""Narrow owner snapshot contract for explicit Shared Work material decisions."""

import hashlib
import json
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.record_repository import RecordRepository, RecordSpec, conflict
from alos.identity import Principal


class BusinessApprovalSubjectPort(Protocol):
    async def approval_subject(
        self,
        session: AsyncSession,
        principal: Principal,
        subject_type: str,
        identity: str,
        requested_action: str | None,
        *,
        mode: str = "read",
    ) -> dict[str, Any]: ...


async def subject_snapshot(
    repository: RecordRepository,
    session: AsyncSession,
    schema: str,
    spec: RecordSpec,
    principal: Principal,
    identity: str,
    requested_action: str | None,
    *,
    mode: str,
    children: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    row = await repository.row(session, schema, spec, principal, identity, lock=mode == "request")
    repository.known_lifecycle(schema, spec, row)
    if mode != "read":
        action = next(
            (a for a in spec.material_actions if a.requested_action == requested_action), None
        )
        if action is None or row[spec.status_field] not in action.source_states:
            raise conflict("Material action is unavailable in the current lifecycle.")
    content: dict[str, Any] = {"record": row}
    if spec.table in children:
        table_name, foreign_key = children[spec.table]
        table = await repository.table(session, schema, table_name)
        records = (
            (
                await session.execute(
                    select(table).where(
                        *repository.scope(table, principal),
                        table.c[foreign_key] == identity,
                    )
                )
            )
            .mappings()
            .all()
        )
        content["children"] = sorted(
            (dict(item) for item in records),
            key=lambda item: json.dumps(item, sort_keys=True, default=str),
        )
    encoded = json.dumps(content, sort_keys=True, default=str, separators=(",", ":")).encode()
    return {"snapshot": hashlib.sha256(encoded).hexdigest(), "status": row[spec.status_field]}
