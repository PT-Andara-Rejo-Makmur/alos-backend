"""Property owns its lifecycle, references and business validation."""

from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from alos.domains.property.records import SPECS
from alos.domains.record_repository import RecordRepository, RecordSpec, authorize, conflict
from alos.identity import Principal


class PropertyService:
    def __init__(self, repository: RecordRepository, **ports: Any) -> None:
        self.repository = repository
        self.ports = ports

    async def listing(
        self, resource: str, principal: Principal, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        authorize(principal, "property", "read")
        return await self.repository.listing("property", SPECS[resource], principal, limit, offset)

    async def detail(self, resource: str, principal: Principal, identity: str) -> dict[str, Any]:
        authorize(principal, "property", "read")
        return await self.repository.detail("property", SPECS[resource], principal, identity)

    async def overview(self, principal: Principal, *, executive: bool = False) -> dict[str, Any]:
        authorize(principal, "property", "read", executive=executive)
        return await self.repository.summary("property", SPECS, principal, company=executive)

    async def mutate(
        self,
        resource: str,
        principal: Principal,
        payload: dict[str, Any],
        identity: str | None = None,
        operation: str = "create",
    ) -> dict[str, Any]:
        authorize(principal, "property", "write")
        return await self.repository.mutate(
            "property", SPECS[resource], principal, payload, identity, operation, self._rule
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
            and old.get("status")
            in {"CLOSED", "COMPLETED", "CANCELLED", "SUBMITTED", "APPROVED", "RESERVED", "SOLD"}
            and operation == "update"
        ):
            raise conflict("Historical or submitted Property records cannot be edited.")
        if data.get("project_id"):
            await self.ports["work"].validate_project_reference(
                session, principal, data["project_id"]
            )
        if name == "construction_updates":
            package = await self.repository.row(
                session,
                "property",
                SPECS["construction_packages"],
                principal,
                data["construction_package_id"],
                lock=True,
            )
            await self.ports["work"].validate_project_reference(
                session, principal, package["project_id"]
            )
            if package["status"] != "IN_PROGRESS":
                raise conflict("Updates require a package in progress.")
            progress = data.get("progress_percent")
            if progress is not None and not Decimal(0) <= progress <= Decimal(100):
                raise conflict("Progress must be between zero and one hundred.")
        if name == "quality_inspections" and old is None:
            values["inspector_actor_id"] = principal.actor_id
        if name == "quality_ncrs" and data.get("inspection_id"):
            inspection = await self.repository.row(
                session, "property", SPECS["quality_inspections"], principal, data["inspection_id"]
            )
            if inspection["project_id"] != data.get("project_id"):
                raise conflict("Inspection and project do not match.")
            if inspection["result"] == "PASS":
                raise conflict("A passed inspection cannot be the source of a nonconformance.")
        if name == "project_milestones":
            if values.get("status") == "COMPLETED" and not data.get("actual_date"):
                raise conflict("Completion requires an authoritative actual date.")
        if (
            name == "project_handovers"
            and values.get("status") == "COMPLETED"
            and not data.get("handover_date")
        ):
            raise conflict("Handover completion requires a date.")
        if (
            name == "payment_certificates"
            and values.get("status") == "SUBMITTED"
            and (data.get("amount") is None or not data.get("period"))
        ):
            raise conflict("Submission requires a recorded amount and period.")
        if (
            name == "change_orders"
            and values.get("status") == "SUBMITTED"
            and data.get("amount_delta") is None
        ):
            raise conflict("Submission requires a recorded amount delta.")
