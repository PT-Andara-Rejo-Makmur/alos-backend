"""Sales owns its lifecycle, references and business validation."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.record_repository import RecordRepository, RecordSpec, authorize, conflict
from alos.domains.sales.records import PIPELINE_EDGES, SPECS
from alos.governance.material_approvals import subject_snapshot
from alos.identity import Principal
from alos.processes.guard import require_reviews
from alos.security.errors import PlatformError


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

    async def business_summary(self, principal: Principal) -> dict[str, Any]:
        from alos.projections.business import business_summary

        return await business_summary(
            self.repository, principal, "sales", executive="EXECUTIVE" in principal.roles
        )

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
            "sales",
            SPECS[resource],
            principal,
            payload,
            identity,
            operation,
            self._rule,
            approvals=self.ports.get("work"),
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
        if name == "bookings" and values.get("status") == "CONFIRMED":
            await require_reviews(
                self.repository, session, principal, "BOOKING", data[spec.identifier]
            )
        if (
            old
            and old.get("status")
            in {"INACTIVE", "CANCELLED", "LOST", "WON", "COMPLETED", "CLOSED", "AKAD_COMPLETED"}
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
                session, "sales", SPECS["bookings"], principal, data["booking_id"], lock=True
            )
            if (
                (values.get("status") == "COMPLETED" and booking["status"] != "CONFIRMED")
                or booking["status"] == "CANCELLED"
                or (
                    name == "closings"
                    and any(
                        booking[key] != data[key] for key in ("customer_id", "property_unit_id")
                    )
                )
            ):
                raise conflict("Booking references do not match.")
            if data.get("closing_date") and data["closing_date"] < booking["booking_date"]:
                raise conflict("Closing date precedes booking date.")
            if name == "financing_contexts" and booking["status"] != "CONFIRMED":
                raise conflict("Konteks pembayaran membutuhkan Booking yang telah dikonfirmasi.")
        if name == "financing_contexts":
            from alos.domains.sales.financing import validate_financing

            await validate_financing(
                self.repository, self.ports["work"], session, principal, data, values, old
            )
        if name == "closings" and values.get("status") == "COMPLETED":
            table = await self.repository.table(session, "sales", "financing_contexts")
            financing = (
                (
                    await session.execute(
                        select(table)
                        .where(
                            *self.repository.scope(table, principal),
                            table.c.booking_id == data["booking_id"],
                        )
                        .with_for_update(read=True)
                    )
                )
                .mappings()
                .first()
            )
            if (
                financing
                and financing["payment_method"] == "KPR"
                and financing["status"] != "AKAD_COMPLETED"
            ):
                raise conflict("KPR dan akad harus selesai sebelum Closing.")
        if name == "bookings" and old and old["status"] == "CONFIRMED" and operation == "update":
            raise conflict("Booking yang dikonfirmasi tidak dapat diubah.")
        if (
            name == "bookings"
            and values.get("status") == "CONFIRMED"
            and data.get("amount") is None
        ):
            raise conflict("Booking confirmation requires an explicit amount.")
        if (
            name == "closings"
            and values.get("status") == "COMPLETED"
            and (data.get("amount") is None or not data.get("closing_date"))
        ):
            raise conflict("Closing completion requires an explicit amount and date.")
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
        if name == "bookings" and values.get("status") == "CONFIRMED":
            await self.ports["units"].synchronize_booking(
                session, principal, data["property_unit_id"], data["booking_id"]
            )
        if name == "closings" and values.get("status") == "COMPLETED":
            await self.ports["units"].synchronize_booking(
                session, principal, data["property_unit_id"], data["booking_id"], sold=True
            )
        if name == "customer_complaints" and old is None:
            values["assigned_to"] = principal.actor_id
        if name == "opportunities":
            if old is None:
                values["stage"] = "Lead"
            if data.get("probability") is not None and not Decimal(0) <= data[
                "probability"
            ] <= Decimal(100):
                raise conflict("Probability must be between zero and one hundred.")
            if values.get("status") == "WON" and data.get("stage") != "Booking":
                raise conflict("Winning requires the recorded Booking pipeline stage.")
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

    async def advance_pipeline(
        self, principal: Principal, identity: str, stage: str
    ) -> dict[str, Any]:
        authorize(principal, "sales", "write")
        async with self.repository.factory() as session, session.begin():
            await self.repository.workspace(session, principal)
            spec = SPECS["opportunities"]
            row = await self.repository.row(session, "sales", spec, principal, identity, lock=True)
            if row["status"] != "OPEN" or stage != PIPELINE_EDGES.get(row["stage"]):
                raise conflict("Pipeline transition or final authority is unavailable.")
            return await self.repository.write(
                session, "sales", spec, principal, {"stage": stage}, identity, operation="pipeline"
            )

    async def approval_subject(
        self,
        session: AsyncSession,
        principal: Principal,
        subject_type: str,
        identity: str,
        requested_action: str | None,
        *,
        mode: str = "read",
    ) -> dict[str, Any]:
        authorize(principal, "sales", "read" if mode == "read" else "write")
        if mode == "decide" and "DIVISION_LEAD" not in principal.roles:
            raise PlatformError(
                "BUSINESS_APPROVAL_DENIED",
                "Owner division lead authority is required.",
                status_code=403,
            )
        spec = next(
            (
                spec
                for spec in SPECS.values()
                if any(action.subject_type == subject_type for action in spec.material_actions)
            ),
            None,
        )
        if spec is None:
            raise conflict("Unsupported approval subject.")
        if mode in {"decide", "execute"} and spec.table == "bookings":
            await require_reviews(self.repository, session, principal, "BOOKING", identity)
        return await subject_snapshot(
            self.repository,
            session,
            "sales",
            spec,
            principal,
            identity,
            requested_action,
            mode=mode,
            children={"pricings": ("pricing_items", "pricing_id")},
        )
