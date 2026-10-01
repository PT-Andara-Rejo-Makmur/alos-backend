"""Sales owns its lifecycle, references and business validation."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.record_repository import RecordRepository, RecordSpec, authorize, conflict
from alos.domains.sales.records import PIPELINE_EDGES, SPECS
from alos.identity import Principal


class SalesService:
    def __init__(self, repository: RecordRepository, **ports: Any) -> None:
        self.repository = repository
        self.ports = ports

    async def listing(
        self, resource: str, principal: Principal, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        authorize(principal, "sales", "read")
        return await self.repository.listing("sales", SPECS[resource], principal, limit, offset)

    async def detail(self, resource: str, principal: Principal, identity: str) -> dict[str, Any]:
        authorize(principal, "sales", "read")
        return await self.repository.detail("sales", SPECS[resource], principal, identity)

    async def overview(self, principal: Principal, *, executive: bool = False) -> dict[str, Any]:
        authorize(principal, "sales", "read", executive=executive)
        return await self.repository.summary("sales", SPECS, principal, company=executive)

    async def mutate(
        self,
        resource: str,
        principal: Principal,
        payload: dict[str, Any],
        identity: str | None = None,
        operation: str = "create",
    ) -> dict[str, Any]:
        authorize(principal, "sales", "write")
        return await self.repository.mutate(
            "sales", SPECS[resource], principal, payload, identity, operation, self._rule
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
        name = spec.table
        if (
            old
            and old.get("status") in {"INACTIVE", "CANCELLED", "LOST", "WON", "COMPLETED", "CLOSED"}
            and operation == "update"
        ):
            raise conflict("Terminal records cannot be edited.")
        if name != "customers" and data.get("customer_id"):
            await self.ports["sales"].validate(session, principal, "customers", data["customer_id"])
        if data.get("lead_id"):
            lead = await self.ports["sales"].validate(session, principal, "leads", data["lead_id"])
            if lead["customer_id"] != data.get("customer_id"):
                raise conflict("Lead and customer do not match.")
        if data.get("opportunity_id"):
            opportunity = await self.ports["sales"].validate(
                session, principal, "opportunities", data["opportunity_id"]
            )
            if opportunity["customer_id"] != data.get("customer_id"):
                raise conflict("Opportunity and customer do not match.")
        if data.get("property_unit_id"):
            unit = await self.ports["units"].validate(
                session, principal, data["property_unit_id"], lock=name == "bookings"
            )
            if (
                old is None
                and name in {"bookings", "site_visits", "pricing_items"}
                and unit["status"] != "AVAILABLE"
            ):
                raise conflict("Property unit is not available.")
        if data.get("booking_id"):
            booking = await self.repository.row(
                session, "sales", SPECS["bookings"], principal, data["booking_id"]
            )
            if booking["status"] == "CANCELLED" or any(
                booking[key] != data[key] for key in ("customer_id", "property_unit_id")
            ):
                raise conflict("Booking references do not match.")
            if data.get("closing_date") and data["closing_date"] < booking["booking_date"]:
                raise conflict("Closing date precedes booking date.")
        if name == "bookings" and old is None:
            table = await self.repository.table(session, "sales", "bookings")
            existing = await session.scalar(
                select(table.c.booking_id).where(
                    table.c.tenant_id == principal.tenant_id,
                    table.c.organization_id == principal.organization_id,
                    table.c.property_unit_id == data["property_unit_id"],
                    table.c.status.in_(("PENDING", "CONFIRMED")),
                )
            )
            if existing is not None:
                raise conflict("Unit already has an active booking.")
        if data.get("campaign_id"):
            await self.ports["marketing"].validate(
                session, principal, "campaigns", data["campaign_id"]
            )
        if data.get("document_id"):
            await self.ports["work"].validate_document_reference(
                session, principal, data["document_id"]
            )
        if name == "leads" and values.get("status") == "QUALIFIED" and not data.get("customer_id"):
            raise conflict("Qualification requires a canonical customer.")
        if name == "leads" and old is None:
            values["owner_actor_id"] = principal.actor_id
        if name == "customer_complaints" and old is None:
            values["assigned_to"] = principal.actor_id
        if name == "opportunities":
            if old is None:
                values["stage"] = "Lead"
            if data.get("probability") is not None and not Decimal(0) <= data[
                "probability"
            ] <= Decimal(100):
                raise conflict("Probability must be between zero and one hundred.")
            if values.get("status") == "WON":
                raise conflict("Final closing authority is unavailable.")
        if name == "customer_followups" and values.get("status") == "COMPLETED":
            values["completed_at"] = datetime.now(UTC)
        if name in {"pricings", "pricing_items"}:
            pricing = (
                data
                if name == "pricings"
                else await self.repository.row(
                    session, "sales", SPECS["pricings"], principal, data["pricing_id"], lock=True
                )
            )
            if (
                pricing.get("effective_from")
                and pricing.get("effective_to")
                and pricing["effective_from"] > pricing["effective_to"]
            ):
                raise conflict("Pricing date range is invalid.")
            if (name == "pricing_items" or operation == "update") and pricing["status"] != "DRAFT":
                raise conflict("Active pricing is immutable.")
            if values.get("status") == "ACTIVE":
                table = await self.repository.table(session, "sales", "pricing_items")
                if (
                    await session.scalar(
                        select(table.c.pricing_item_id)
                        .where(
                            *self.repository.scope(table, principal),
                            table.c.pricing_id == data["pricing_id"],
                        )
                        .limit(1)
                    )
                    is None
                ):
                    raise conflict("Pricing requires at least one item.")

    async def advance_pipeline(
        self, principal: Principal, identity: str, stage: str
    ) -> dict[str, Any]:
        authorize(principal, "sales", "write")
        async with self.repository.factory() as session, session.begin():
            await self.repository.workspace(session, principal)
            spec = SPECS["opportunities"]
            row = await self.repository.row(session, "sales", spec, principal, identity, lock=True)
            if (
                row["status"] != "OPEN"
                or stage != PIPELINE_EDGES.get(row["stage"])
            ):
                raise conflict("Pipeline transition or final authority is unavailable.")
            return await self.repository.write(
                session, "sales", spec, principal, {"stage": stage}, identity, operation="pipeline"
            )
