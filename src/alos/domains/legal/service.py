"""Legal owns its operational records, scope and conservative internal policy."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.contracts import ContractValidationError
from alos.domains.legal.records import SPECS
from alos.domains.record_references import DocumentReferencePort
from alos.domains.record_repository import RecordRepository, RecordSpec, authorize, conflict
from alos.identity import Principal
from alos.security.errors import PlatformError


class LegalService:
    def __init__(self, repository: RecordRepository, *, work: DocumentReferencePort) -> None:
        self.repository = repository
        self.work = work

    async def listing(
        self, resource: str, principal: Principal, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        authorize(principal, "legal", "read")
        return await self.repository.listing("legal", SPECS[resource], principal, limit, offset)

    async def detail(self, resource: str, principal: Principal, identity: str) -> dict[str, Any]:
        authorize(principal, "legal", "read")
        return await self.repository.detail("legal", SPECS[resource], principal, identity)

    async def overview(self, principal: Principal, *, executive: bool = False) -> dict[str, Any]:
        authorize(principal, "legal", "read", executive=executive)
        return await self.repository.summary("legal", SPECS, principal, company=executive)

    async def mutate(
        self,
        resource: str,
        principal: Principal,
        payload: dict[str, Any],
        identity: str | None = None,
        operation: str = "create",
    ) -> dict[str, Any]:
        authorize(principal, "legal", "write")
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
                "https://schemas.alos.dev/v1/legal/legal-contracts.schema.json#/$defs/"
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
        return await self.repository.mutate(
            "legal",
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
            table = await self.repository.table(session, "legal", name)
            for field in (
                "owner_actor_id",
                "reviewer_actor_id",
                "interviewer_actor_id",
                "assigned_to",
            ):
                if field in table.c:
                    values[field] = principal.actor_id
        if name == "due_diligence_items":
            parent = await self.repository.row(
                session,
                "legal",
                SPECS["due_diligences"],
                principal,
                data["due_diligence_id"],
                lock=True,
            )
            self.repository.known_lifecycle("legal", SPECS["due_diligences"], parent)
        if name in {"due_diligences", "claim_reviews", "expiries"}:
            resources = {
                "CONTRACT": "contracts",
                "PERMIT": "permits",
                "LAND_DOCUMENT": "land_documents",
                "CASE": "cases",
            }
            resource = resources.get(data["subject_type"])
            if resource is None:
                raise conflict("Only scoped internal Legal subject references are available.")
            await self.repository.row(
                session, "legal", SPECS[resource], principal, data["subject_id"], lock=True
            )
        # property_ref is opaque recorded metadata: no fabricated FK or Property write port.
        if name in {"legal_reviews", "contract_revisions"}:
            await self.repository.row(
                session, "legal", SPECS["contracts"], principal, data["contract_id"], lock=True
            )
        if name == "legal_reviews" and values.get("status") == "REVIEWED":
            if not data.get("review_summary") or not data.get("assessment"):
                raise conflict("Legal review requires an explicit assessment and summary.")
            values["reviewed_by"] = principal.actor_id
            values["reviewed_at"] = datetime.now(UTC)
        if name == "contract_revisions":
            await self.work.validate_document_version_reference(
                session, principal, data["document_id"], data["document_version"]
            )
            if data["recorded_on"] > datetime.now(UTC).date():
                raise conflict("Contract revision evidence cannot be recorded in the future.")
        if name == "contracts" and operation == "update":
            revisions = await self.repository.table(session, "legal", "contract_revisions")
            if await session.scalar(
                select(revisions.c.contract_revision_id)
                .where(
                    *self.repository.scope(revisions, principal),
                    revisions.c.contract_id == data["contract_id"],
                )
                .limit(1)
            ):
                raise conflict("A contract with revision evidence cannot be overwritten.")
        # Risk likelihood, impact and rating are explicit inputs; no derived score exists.
