"""Hr owns its operational records, scope and conservative internal policy."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from alos.contracts import ContractValidationError
from alos.domains.hr.records import SPECS
from alos.domains.record_references import DocumentReferencePort
from alos.domains.record_repository import RecordRepository, RecordSpec, authorize, conflict
from alos.identity import Principal
from alos.security.errors import PlatformError


class HrService:
    def __init__(self, repository: RecordRepository, *, work: DocumentReferencePort) -> None:
        self.repository = repository
        self.work = work

    async def listing(
        self, resource: str, principal: Principal, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        authorize(principal, "hr", "read")
        return await self.repository.listing("hr", SPECS[resource], principal, limit, offset)

    async def detail(self, resource: str, principal: Principal, identity: str) -> dict[str, Any]:
        authorize(principal, "hr", "read")
        return await self.repository.detail("hr", SPECS[resource], principal, identity)

    async def overview(self, principal: Principal, *, executive: bool = False) -> dict[str, Any]:
        authorize(principal, "hr", "read", executive=executive)
        return await self.repository.summary("hr", SPECS, principal, company=executive)

    async def mutate(
        self,
        resource: str,
        principal: Principal,
        payload: dict[str, Any],
        identity: str | None = None,
        operation: str = "create",
    ) -> dict[str, Any]:
        authorize(principal, "hr", "write")
        spec = SPECS[resource]
        if (
            operation not in {"create", "update", "transition"}
            or (operation == "update" and (spec.immutable or not spec.update_fields))
            or (operation == "transition" and not spec.transitions)
        ):
            raise conflict("Operation is unavailable for this record.")
        contracts = self.repository.contracts
        if contracts is None:
            raise PlatformError(
                "CONTRACTS_UNAVAILABLE", "Canonical contracts are required.", status_code=503
            )
        try:
            contracts.validate(
                "https://schemas.alos.dev/v1/hr/hr-contracts.schema.json#/$defs/"
                + spec.name
                + operation.title()
                + "Request",
                payload,
            )
        except ContractValidationError as exc:
            raise PlatformError(
                "BUSINESS_CONTRACT_INVALID", "Invalid canonical request.", status_code=422
            ) from exc
        except ValueError as exc:
            raise PlatformError(
                "CONTRACTS_UNAVAILABLE", "Canonical contracts are required.", status_code=503
            ) from exc
        values = dict(payload)
        if "recorded_status" in values:
            values["status"] = values.pop("recorded_status")
        if resource == "employees" and operation == "transition":
            values["employment_status"] = values.pop("status")
        return await self.repository.mutate(
            "hr",
            spec,
            principal,
            values,
            identity,
            operation,
            self._rule,
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
        if old and old.get(spec.status_field) in {
            "ARCHIVED",
            "CLOSED",
            "COMPLETED",
            "CANCELLED",
            "REVIEWED",
            "WITHDRAWN",
        }:
            raise conflict("Terminal recorded evidence cannot be edited.")
        for start, end in (
            ("start_date", "end_date"),
            ("join_date", "end_date"),
            ("issued_at", "expires_at"),
            ("started_at", "finished_at"),
            ("check_in_at", "check_out_at"),
        ):
            if (
                data.get(start) is not None
                and data.get(end) is not None
                and data[start] > data[end]
            ):
                raise conflict("Recorded date range is invalid.")
        if data.get("document_id"):
            await self.work.validate_document_reference(session, principal, data["document_id"])
        if old is None:
            # Ownership means the author of this internal record, never decision authority.
            table = await self.repository.table(session, "hr", name)
            for field in (
                "owner_actor_id",
                "reviewer_actor_id",
                "interviewer_actor_id",
                "assigned_to",
            ):
                if field in table.c:
                    values[field] = principal.actor_id
        references = {
            "employee_id": "employees",
            "recruitment_id": "recruitments",
            "candidate_id": "candidates",
            "training_id": "trainings",
            "succession_id": "successions",
        }
        parents = {}
        for field, resource in sorted(references.items()):
            if data.get(field) and field != spec.identifier:
                parent = await self.repository.row(
                    session, "hr", SPECS[resource], principal, data[field], lock=True
                )
                self.repository.known_lifecycle("hr", SPECS[resource], parent)
                parents[resource] = parent
        if name == "candidates" and parents.get("recruitments", {}).get("status") in {
            "CLOSED",
            "ON_HOLD",
        }:
            raise conflict("Recruitment does not accept internal candidate progression.")
        if name == "interviews" and parents["candidates"]["status"] not in {
            "SCREENING",
            "INTERVIEW",
        }:
            raise conflict("Interview requires internal candidate screening.")
        if name == "interviews" and values.get("status") == "COMPLETED" and not data.get("notes"):
            raise conflict("Interview completion requires explicit notes.")
        if (
            name == "performance_reviews"
            and values.get("status") == "REVIEWED"
            and (data.get("rating") is None or not data.get("summary"))
        ):
            raise conflict("Internal review requires an explicit rating and summary.")
        if name == "training_enrollments":
            if parents["trainings"]["status"] not in {"PLANNED", "IN_PROGRESS"}:
                raise conflict("Training is not open for enrollment progression.")
            if values.get("status") == "COMPLETED":
                values["completed_at"] = datetime.now(UTC)
        if name in {"attendances", "leave_requests", "onboardings", "training_enrollments"}:
            if parents["employees"]["employment_status"] != "ACTIVE":
                raise conflict("An active employee record is required.")
        # Employee actor associations remain Identity-owned; HR never changes auth state.
        # Leave approval and hiring/signing decisions have no available command here.
