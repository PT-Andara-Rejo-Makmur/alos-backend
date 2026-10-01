"""Marketing-owned scoped references."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.marketing.records import SPECS
from alos.domains.record_repository import RecordRepository, conflict
from alos.identity import Principal


class MarketingReferences:
    def __init__(self, repository: RecordRepository) -> None:
        self.repository = repository

    async def validate(
        self, session: AsyncSession, principal: Principal, resource: str, identity: str
    ) -> dict[str, Any]:
        row = await self.repository.row(session, "marketing", SPECS[resource], principal, identity)
        self.repository.known_lifecycle("marketing", SPECS[resource], row)
        if row.get("status") in {"INACTIVE", "CANCELLED", "COMPLETED"}:
            raise conflict("Marketing reference is inactive.")
        return row
