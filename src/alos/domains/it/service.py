"""It owns its operational records, scope and conservative internal policy."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.contracts import ContractValidationError
from alos.domains.it.records import SPECS
from alos.domains.record_references import DocumentReferencePort
from alos.domains.record_repository import RecordRepository, RecordSpec, authorize, conflict
from alos.identity import Principal
from alos.security.errors import PlatformError


class ItService:
    def __init__(self, repository: RecordRepository, *, work: DocumentReferencePort) -> None:
        self.repository = repository
        self.work = work

    async def _authorize(
        self, principal: Principal, action: str, *, executive: bool = False
    ) -> None:
        authorize(principal, "it", action, executive=executive)
        if not executive and "IT_ADMIN" in principal.roles:
            async with self.repository.factory() as session:
                workspaces = await self.repository.table(session, "core", "workspaces")
                kind = await session.scalar(
                    select(workspaces.c.workspace_type).where(
                        *self.repository.scope(workspaces, principal), workspaces.c.active.is_(True)
                    )
                )
                if kind != "IT_OPERATIONS":
                    raise PlatformError(
                        "BUSINESS_AUTHORITY_DENIED",
                        "IT administration requires IT Operations.",
                        status_code=403,
                    )

    async def listing(
        self, resource: str, principal: Principal, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        await self._authorize(principal, "read")
        return await self.repository.listing("it", SPECS[resource], principal, limit, offset)

    async def detail(self, resource: str, principal: Principal, identity: str) -> dict[str, Any]:
        await self._authorize(principal, "read")
        return await self.repository.detail("it", SPECS[resource], principal, identity)

    async def overview(self, principal: Principal, *, executive: bool = False) -> dict[str, Any]:
        await self._authorize(principal, "read", executive=executive)
        return await self.repository.summary("it", SPECS, principal, company=executive)

    async def mutate(
        self,
        resource: str,
        principal: Principal,
        payload: dict[str, Any],
        identity: str | None = None,
        operation: str = "create",
    ) -> dict[str, Any]:
        await self._authorize(principal, "write")
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
                "https://schemas.alos.dev/v1/it/it-contracts.schema.json#/$defs/"
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
            "it",
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
        if name == "releases" and values.get("status") == "READY":
            runs = await self.repository.table(session, "it", "ci_runs")
            pipelines = await self.repository.table(session, "it", "cicd_pipelines")
            passed = await session.scalar(
                select(runs.c.ci_run_id)
                .join(pipelines, pipelines.c.pipeline_id == runs.c.pipeline_id)
                .where(
                    *self.repository.scope(runs, principal),
                    *self.repository.scope(pipelines, principal),
                    runs.c.ci_run_id == data.get("ci_run_id"),
                    runs.c.status == "SUCCEEDED",
                    pipelines.c.repository_id == data["repository_id"],
                )
            )
            if passed is None:
                raise conflict("Hasil pengujian repository yang berhasil diperlukan.")
        if name == "releases" and values.get("status") in {"DEPLOYED", "FAILED", "ROLLED_BACK"}:
            if not data.get("deployment_reference"):
                raise conflict("Bukti pelaksanaan rilis diperlukan.")
        if (
            name == "releases"
            and values.get("status") == "VERIFIED"
            and not data.get("verification_notes")
        ):
            raise conflict("Hasil verifikasi rilis diperlukan.")
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
            table = await self.repository.table(session, "it", name)
            for field in (
                "owner_actor_id",
                "reviewer_actor_id",
                "interviewer_actor_id",
                "assigned_to",
            ):
                if field in table.c:
                    values[field] = principal.actor_id
        references = {
            "system_id": "systems",
            "repository_id": "repositories",
            "environment_id": "environments",
            "pipeline_id": "cicd_pipelines",
            "backup_policy_id": "backup_policies",
            "backup_run_id": "backup_runs",
        }
        for field, resource in sorted(references.items()):
            if data.get(field) and field != spec.identifier:
                parent = await self.repository.row(
                    session, "it", SPECS[resource], principal, data[field], lock=True
                )
                self.repository.known_lifecycle("it", SPECS[resource], parent)
        if name == "service_monitors":
            if data.get("last_checked_at") is None or data["last_checked_at"] > datetime.now(UTC):
                raise conflict("Monitoring requires an explicit recorded source timestamp.")
        if (
            name == "backup_runs"
            and data["status"] == "SUCCEEDED"
            and (not data.get("finished_at") or not data.get("artifact_ref"))
        ):
            raise conflict("Recorded backup success requires completion and artifact evidence.")
        if (
            name == "ci_runs"
            and data["status"] in {"SUCCEEDED", "FAILED"}
            and not data.get("finished_at")
        ):
            raise conflict("Recorded CI outcome requires a completion timestamp.")
        if name == "restore_tests" and data["result"] == "PASSED" and not data.get("notes"):
            raise conflict("Restore evidence requires explicit recorded notes.")
        if values.get("status") == "RESOLVED" and not data.get("description"):
            raise conflict("Internal resolution requires explicit evidence notes.")
        # Integrations, pipelines, backup and releases are metadata only. No execution port.
        # Production approval/release/rollback remain exclusively in Governance.
