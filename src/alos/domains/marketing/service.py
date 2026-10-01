"""Marketing owns its lifecycle, references and business validation."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.marketing.records import SPECS
from alos.domains.record_repository import RecordRepository, RecordSpec, authorize, conflict
from alos.identity import Principal


class MarketingService:
    def __init__(self, repository: RecordRepository, **ports: Any) -> None:
        self.repository = repository
        self.ports = ports

    async def listing(
        self, resource: str, principal: Principal, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        authorize(principal, "marketing", "read")
        return await self.repository.listing("marketing", SPECS[resource], principal, limit, offset)

    async def detail(self, resource: str, principal: Principal, identity: str) -> dict[str, Any]:
        authorize(principal, "marketing", "read")
        return await self.repository.detail("marketing", SPECS[resource], principal, identity)

    async def overview(self, principal: Principal, *, executive: bool = False) -> dict[str, Any]:
        authorize(principal, "marketing", "read", executive=executive)
        return await self.repository.summary("marketing", SPECS, principal, company=executive)

    async def mutate(
        self,
        resource: str,
        principal: Principal,
        payload: dict[str, Any],
        identity: str | None = None,
        operation: str = "create",
    ) -> dict[str, Any]:
        authorize(principal, "marketing", "write")
        return await self.repository.mutate(
            "marketing", SPECS[resource], principal, payload, identity, operation, self._rule
        )

    async def _rule(
        self,
        session: AsyncSession,
        spec: RecordSpec,
        principal: Principal,
        values: dict[str, Any],
        old: dict[str, Any] | None,
        operation: str,
    ) -> None:
        data = {**(old or {}), **values}
        if (
            old
            and old.get("status") in {"COMPLETED", "CANCELLED", "ARCHIVED", "PUBLISHED", "INACTIVE"}
            and operation == "update"
        ):
            raise conflict("Published or terminal marketing history cannot be edited.")
        if (
            data.get("start_date")
            and data.get("end_date")
            and data["start_date"] > data["end_date"]
        ):
            raise conflict("Campaign date range is invalid.")
        for field, resource in (("campaign_id", "campaigns"), ("channel_id", "channels")):
            if spec.table != resource and data.get(field):
                await self.ports["marketing"].validate(session, principal, resource, data[field])
        if spec.table == "attributions":
            if not any(data.get(key) for key in ("customer_id", "lead_id")) or not any(
                data.get(key) for key in ("campaign_id", "channel_id")
            ):
                raise conflict("Attribution requires a Sales subject and a Marketing source.")
            customer = data.get("customer_id")
            if customer:
                await self.ports["sales"].validate(session, principal, "customers", customer)
            if data.get("lead_id"):
                lead = await self.ports["sales"].validate(
                    session, principal, "leads", data["lead_id"]
                )
                if customer and lead["customer_id"] != customer:
                    raise conflict("Attribution lead and customer do not match.")
            if data["occurred_at"] > datetime.now(UTC):
                raise conflict("Attribution must describe an occurred event.")
        if spec.table == "contents" and values.get("status") == "PUBLISHED":
            values["published_at"] = datetime.now(UTC)
