"""Hr owns its operational records, scope and conservative internal policy."""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.contracts import ContractValidationError
from alos.domains.hr.records import SPECS
from alos.domains.record_references import DocumentReferencePort
from alos.domains.record_repository import RecordRepository, RecordSpec, authorize, conflict
from alos.identity import Principal
from alos.processes.authority import revalidate
from alos.processes.guard import require_reviews
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
        decision_reason = values.pop("decision_reason", None)

        async def rule(
            session: AsyncSession,
            spec: RecordSpec,
            actor: Principal,
            data: dict[str, Any],
            old: dict[str, Any] | None,
            mode: str,
        ) -> None:
            await self._rule(session, spec, actor, data, old, mode)
            if resource == "leave_requests" and data.get("status") in {"APPROVED", "REJECTED"}:
                if not decision_reason or "DIVISION_LEAD" not in actor.roles:
                    raise PlatformError(
                        "LEAVE_DECISION_DENIED",
                        "Keputusan kepala divisi dan alasan diperlukan.",
                        status_code=403,
                    )
                data.update(
                    approved_by=actor.actor_id,
                    approved_at=datetime.now(UTC),
                    decision_reason=decision_reason,
                )

        return await self.repository.mutate(
            "hr",
            spec,
            principal,
            values,
            identity,
            operation,
            rule,
        )

    async def hire(
        self, principal: Principal, candidate_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        authorize(principal, "hr", "write")
        if "DIVISION_LEAD" not in principal.roles:
            raise PlatformError(
                "HIRING_DECISION_DENIED", "Kewenangan kepala divisi HR diperlukan.", status_code=403
            )
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            candidate = await self.repository.row(
                session, "hr", SPECS["candidates"], principal, candidate_id, lock=True
            )
            if candidate.get("employee_id"):
                employee = await self.repository.row(
                    session, "hr", SPECS["employees"], principal, candidate["employee_id"]
                )
                if employee["employee_number"] != values["employee_number"] or employee[
                    "join_date"
                ] != date.fromisoformat(values["join_date"]):
                    raise conflict("Kandidat telah direkrut dengan data karyawan yang berbeda.")
                return self.repository.project(SPECS["employees"], employee, principal)
            if candidate["status"] not in {"INTERVIEW", "OFFERED"}:
                raise conflict("Kandidat belum siap untuk keputusan penerimaan.")
            recruitment = await self.repository.row(
                session,
                "hr",
                SPECS["recruitments"],
                principal,
                candidate["recruitment_id"],
                lock=True,
            )
            if recruitment["status"] != "OPEN":
                raise conflict("Rekrutmen tidak aktif.")
            interviews = await self.repository.table(session, "hr", "interviews")
            completed = await session.scalar(
                select(interviews.c.interview_id)
                .where(
                    *self.repository.scope(interviews, principal),
                    interviews.c.candidate_id == candidate_id,
                    interviews.c.status == "COMPLETED",
                )
                .limit(1)
            )
            if completed is None:
                raise conflict("Hasil wawancara belum tercatat.")
            employee = await self.repository.write(
                session,
                "hr",
                SPECS["employees"],
                principal,
                {
                    "employee_number": values["employee_number"],
                    "full_name": candidate["full_name"],
                    "email": candidate["email"],
                    "department_code": recruitment["department_code"],
                    "position_title": recruitment["position_title"],
                    "join_date": date.fromisoformat(values["join_date"]),
                },
                None,
                operation="hired",
                audit_reason=values["reason"],
            )
            await self.repository.write(
                session,
                "hr",
                SPECS["candidates"],
                principal,
                {"status": "HIRED", "employee_id": employee["employee_id"]},
                candidate_id,
                operation="hiring_decision",
                audit_reason=values["reason"],
            )
            return employee

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
        if name == "employment_contracts":
            if operation == "update" and old and old["status"] != "DRAFT":
                raise conflict("Perubahan kontrak membutuhkan pengembalian ke draf.")
            target = values.get("status")
            if (
                target in {"APPROVED", "ACTIVE", "EXPIRED"}
                and "DIVISION_LEAD" not in principal.roles
            ):
                raise PlatformError(
                    "CONTRACT_DECISION_DENIED", "Keputusan kepala HR diperlukan.", status_code=403
                )
            if target == "DRAFT" and old and old["status"] == "IN_REVIEW":
                processes = await self.repository.table(session, "core", "business_processes")
                returned = await session.scalar(
                    select(processes.c.process_id).where(
                        *self.repository.scope(processes, principal),
                        processes.c.business_type == "EMPLOYMENT_CONTRACT",
                        processes.c.subject_id == data[spec.identifier],
                        processes.c.status == "RETURNED",
                    )
                )
                if not returned:
                    raise conflict("Pemeriksaan harus dikembalikan sebelum kontrak diperbaiki.")
            if target == "APPROVED":
                if data.get("legal_review_required"):
                    policies = await self.repository.table(
                        session, "core", "business_process_policies"
                    )
                    configured = await session.scalar(
                        select(policies.c.policy_id)
                        .where(
                            *self.repository.scope(policies, principal),
                            policies.c.business_type == "EMPLOYMENT_CONTRACT",
                            policies.c.active.is_(True),
                        )
                        .with_for_update(read=True)
                    )
                    if not configured:
                        raise conflict(
                            "Aturan pemeriksaan Legal harus dikonfigurasi terlebih dahulu."
                        )
                await require_reviews(
                    self.repository,
                    session,
                    principal,
                    "EMPLOYMENT_CONTRACT",
                    data[spec.identifier],
                )
            if target == "ACTIVE":
                documents = await self.repository.table(session, "core", "documents")
                approved = await session.scalar(
                    select(documents.c.document_id)
                    .where(
                        *self.repository.scope(documents, principal),
                        documents.c.document_id == data.get("document_id"),
                        documents.c.status == "APPROVED",
                    )
                    .with_for_update(read=True)
                )
                if not approved:
                    raise conflict("Kontrak aktif membutuhkan dokumen yang telah disetujui.")
        if name == "onboardings" and values.get("status") == "COMPLETED":
            await require_reviews(
                self.repository, session, principal, "ONBOARDING", data["onboarding_id"]
            )
        if name == "employees" and values.get("employment_status") == "INACTIVE":
            await require_reviews(
                self.repository, session, principal, "OFFBOARDING", data["employee_id"]
            )
            if data.get("actor_id"):
                for table_name in ("auth_accounts", "workspace_memberships", "auth_sessions"):
                    table = await self.repository.table(session, "core", table_name)
                    active = await session.scalar(
                        select(table.c.actor_id)
                        .where(
                            table.c.tenant_id == principal.tenant_id,
                            table.c.organization_id == principal.organization_id,
                            table.c.actor_id == data["actor_id"],
                            table.c.active.is_(True),
                        )
                        .limit(1)
                    )
                    if active:
                        raise conflict(
                            "Akses dan sesi harus dinonaktifkan melalui Identity terlebih dahulu."
                        )
        if (
            old
            and not (name == "employment_contracts" and operation == "transition")
            and old.get(spec.status_field)
            in {
                "ARCHIVED",
                "CLOSED",
                "COMPLETED",
                "CANCELLED",
                "REVIEWED",
                "WITHDRAWN",
                "APPROVED",
                "REJECTED",
                "HIRED",
            }
        ):
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
        if name == "recruitments" and data.get("requesting_workspace_id"):
            table = await self.repository.table(session, "core", "workspaces")
            target = await session.scalar(
                select(table.c.workspace_id).where(
                    table.c.tenant_id == principal.tenant_id,
                    table.c.organization_id == principal.organization_id,
                    table.c.workspace_id == data["requesting_workspace_id"],
                    table.c.active.is_(True),
                )
            )
            if target is None:
                raise conflict("Divisi pemohon berada di luar perusahaan atau tidak aktif.")
        references = {
            "employee_id": "employees",
            "recruitment_id": "recruitments",
            "candidate_id": "candidates",
            "training_id": "trainings",
            "succession_id": "successions",
            "inventory_item_id": "inventory_items",
            "facility_request_id": "facility_requests",
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
        if name == "asset_handovers" and data.get("event") == "GIVEN":
            if parents["employees"]["employment_status"] != "ACTIVE":
                raise conflict("Aset hanya dapat diberikan kepada karyawan aktif.")
        if name == "leave_requests" and values.get("status") in {"APPROVED", "REJECTED"}:
            if parents["employees"].get("actor_id") == principal.actor_id:
                raise PlatformError(
                    "LEAVE_SELF_DECISION_DENIED",
                    "Keputusan cuti membutuhkan pemeriksa lain.",
                    status_code=403,
                )
        # Employee actor associations remain Identity-owned; HR never changes auth state.
        if (
            name == "facility_requests"
            and values.get("status") == "COMPLETED"
            and not data.get("resolution_notes")
        ):
            raise conflict("Facility completion requires explicit resolution notes.")
        for field in ("recorded_on", "performed_on", "handover_on", "assessed_on"):
            if data.get(field) is not None and data[field] > datetime.now(UTC).date():
                raise conflict("Historical GA evidence cannot be recorded in the future.")
