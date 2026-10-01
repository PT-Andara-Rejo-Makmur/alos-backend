"""Sales-owned scoped reference validation, shared without service coupling."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.record_repository import RecordRepository, conflict
from alos.domains.sales.records import SPECS
from alos.identity import Principal


class SalesReferences:
    def __init__(self, repository: RecordRepository) -> None:
        self.repository = repository

    async def validate(
        self, session: AsyncSession, principal: Principal, resource: str, identity: str
    ) -> dict[str, Any]:
        row = await self.repository.row(session, "sales", SPECS[resource], principal, identity)
        self.repository.known_lifecycle("sales", SPECS[resource], row)
        if row.get("status") in {"INACTIVE", "CANCELLED"}:
            raise conflict("Sales reference is inactive.")
        return row
