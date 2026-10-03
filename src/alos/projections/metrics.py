"""Bounded domain adapters for version-bound Strategy actuals."""

import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from alos.audit import AuditEvent
from alos.domains.record_repository import RecordRepository, authorize, conflict
from alos.domains.strategy.authority import authorize as strategy_authorize
from alos.domains.strategy.models import Observation, ObservationKind, Target, VerificationState
from alos.domains.strategy.repository import SqlStrategyRepository
from alos.domains.strategy.service import StrategyService
from alos.identity import Principal
from alos.observability.correlation import current_correlation_id
from alos.processes.authority import revalidate


class BusinessMetricService:
    def __init__(self, records: RecordRepository, strategy: StrategyService) -> None:
        self.records = records
        self.strategy = strategy

    def session(self) -> AsyncSession:
        repository = self.strategy.repository
        if not isinstance(repository, SqlStrategyRepository) or repository.current_session is None:
            raise conflict("Perhitungan bisnis membutuhkan penyimpanan Strategy persisten.")
        return repository.current_session

    async def bind(
        self, principal: Principal, target_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        target = await self.strategy.get_target(principal, target_id, values["target_version"])
        strategy_authorize(
            principal,
            "division_manage",
            tenant_id=target.tenant_id,
            organization_id=target.organization_id,
            owner_workspace_id=target.owner_workspace_id,
        )
        authorize(principal, "sales", "read")
        if target.unit != ("COUNT" if values["metric"] == "CLOSING_COUNT" else "IDR"):
            raise conflict("Satuan target harus sesuai dengan sumber Closing: COUNT atau IDR.")
        if target.scope.type != "DIVISION" or target.owner_workspace_id != principal.workspace_id:
            raise conflict("Sumber Closing harus terikat ke target divisi pemilik data.")
        async with self.records.factory() as session, session.begin():
            await revalidate(self.records, session, principal)
            table = await self.records.table(session, "strategy", "source_bindings")
            targets = await self.records.table(session, "strategy", "targets")
            await session.execute(
                select(targets)
                .where(
                    *self.records.scope(targets, principal),
                    targets.c.target_id == target_id,
                    targets.c.version == target.version,
                )
                .with_for_update()
            )
            old = (
                (
                    await session.execute(
                        select(table).where(
                            *self.records.scope(table, principal),
                            table.c.target_id == target_id,
                            table.c.target_version == target.version,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if old:
                if old["metric"] != values["metric"]:
                    raise conflict(
                        "Sumber target tersimpan; perubahan membutuhkan versi target baru."
                    )
                return {
                    "binding_id": old["binding_id"],
                    "target_id": target_id,
                    "target_version": target.version,
                    "metric": old["metric"],
                }
            binding_id = uuid4().hex
            await session.execute(
                insert(table).values(
                    binding_id=binding_id,
                    tenant_id=principal.tenant_id,
                    organization_id=principal.organization_id,
                    workspace_id=principal.workspace_id,
                    target_id=target_id,
                    target_version=target.version,
                    metric=values["metric"],
                    created_by=principal.actor_id,
                    created_at=datetime.now(UTC),
                    reason=values["reason"],
                )
            )
            await self.records.audit.append_in_session(
                session,
                AuditEvent(
                    event_type="strategy.business_source_bound",
                    entity_type="strategy_source_binding",
                    entity_id=binding_id,
                    tenant_id=principal.tenant_id,
                    organization_id=principal.organization_id,
                    workspace_id=principal.workspace_id,
                    actor_id=principal.actor_id,
                    correlation_id=current_correlation_id(),
                    outcome="SUCCEEDED",
                    occurred_at=datetime.now(UTC),
                    reason=values["reason"],
                    metadata={
                        "target_id": target_id,
                        "target_version": target.version,
                        "metric": values["metric"],
                    },
                ),
            )
            return {
                "binding_id": binding_id,
                "target_id": target_id,
                "target_version": target.version,
                "metric": values["metric"],
            }

    async def calculate(self, principal: Principal, target: Target, request_id: str) -> Observation:
        authorize(principal, "sales", "read")
        session = self.session()
        await revalidate(self.records, session, principal)
        bindings = await self.records.table(session, "strategy", "source_bindings")
        binding = (
            (
                await session.execute(
                    select(bindings)
                    .where(
                        *self.records.scope(bindings, principal),
                        bindings.c.target_id == target.target_id,
                        bindings.c.target_version == target.version,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if binding is None:
            raise conflict("Sumber perhitungan target belum dikonfigurasi.")
        calculations = await self.records.table(session, "strategy", "business_calculations")
        old = (
            (
                await session.execute(
                    select(calculations).where(
                        *self.records.scope(calculations, principal),
                        calculations.c.binding_id == binding["binding_id"],
                        calculations.c.request_id == request_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if old:
            if old["actor_id"] != principal.actor_id:
                raise conflict("Permintaan perhitungan telah digunakan pengguna lain.")
            observations = await self.strategy.repository.list_observations(
                target.target_id, target.version
            )
            return next(
                item for item in observations if item.observation_id == old["observation_id"]
            )
        closings = await self.records.table(session, "sales", "closings")
        rows = (
            (
                await session.execute(
                    select(closings)
                    .where(
                        *self.records.scope(closings, principal),
                        closings.c.status == "COMPLETED",
                        closings.c.closing_date >= date.fromisoformat(target.period.starts_at[:10]),
                        closings.c.closing_date <= date.fromisoformat(target.period.ends_at[:10]),
                    )
                    .order_by(closings.c.closing_id)
                    .with_for_update(read=True)
                )
            )
            .mappings()
            .all()
        )
        if binding["metric"] == "CLOSING_VALUE" and any(row["amount"] is None for row in rows):
            raise conflict("Nilai Closing belum lengkap; gunakan data manual dengan bukti.")
        value = (
            Decimal(len(rows))
            if binding["metric"] == "CLOSING_COUNT"
            else sum(
                (Decimal(row["amount"]) for row in rows),
                Decimal(0),
            )
        )
        # Store only the business facts used by the formula, not customer or payment details.
        snapshot = {
            "target_id": target.target_id,
            "target_version": target.version,
            "period": {
                "starts_at": target.period.starts_at,
                "ends_at": target.period.ends_at,
                "granularity": target.period.granularity,
            },
            "unit": target.unit,
            "value": str(value),
            "metric": binding["metric"],
            "source_rows": [
                {
                    "closing_id": row["closing_id"],
                    "updated_at": str(row["updated_at"]),
                    "amount": str(row["amount"]),
                }
                for row in rows
            ],
        }
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        calculation_id, observation_id, evidence_id = (uuid4().hex for _ in range(3))
        now = datetime.now(UTC)
        await session.execute(
            insert(calculations).values(
                calculation_id=calculation_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                binding_id=binding["binding_id"],
                request_id=request_id,
                actor_id=principal.actor_id,
                observation_id=observation_id,
                evidence_id=evidence_id,
                snapshot=snapshot,
                content_hash=digest,
                calculated_at=now,
            )
        )
        evidence = await self.records.table(session, "evidence", "evidence_refs")
        await session.execute(
            insert(evidence).values(
                evidence_id=evidence_id,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                source_id=calculation_id,
                source_version="1",
                uri=f"alos://business-calculations/{calculation_id}",
                content_hash="sha256:" + digest,
                data_classification="INTERNAL",
                validation_status="VALID",
                metadata_payload={
                    "metric": binding["metric"],
                    "target_id": target.target_id,
                    "target_version": target.version,
                },
                captured_at=now,
            )
        )
        return Observation(
            observation_id=observation_id,
            target_id=target.target_id,
            target_version=target.version,
            kind=ObservationKind.ACTUAL,
            value=value,
            unit=target.unit,
            period=target.period,
            source_ref=calculation_id,
            source_mode="SOURCE_LINKED",
            observed_at=now,
            verification_state=VerificationState.PENDING_VERIFICATION,
            evidence_refs=(evidence_id,),
            actor_id=principal.actor_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            owner_workspace_id=principal.workspace_id,
            correlation_id=current_correlation_id(),
        )

    async def validate(self, observation: Observation, target: Target) -> bool:
        table = await self.records.table(self.session(), "strategy", "business_calculations")
        row = (
            (
                await self.session().execute(
                    select(table).where(
                        table.c.calculation_id == observation.source_ref,
                        table.c.tenant_id == target.tenant_id,
                        table.c.organization_id == target.organization_id,
                        table.c.workspace_id == target.owner_workspace_id,
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None or observation.source_mode != "SOURCE_LINKED":
            return False
        snapshot = row["snapshot"]
        return bool(
            snapshot["target_id"] == target.target_id
            and snapshot["target_version"] == target.version
            and Decimal(snapshot["value"]) == observation.value
            and snapshot["unit"] == observation.unit
            and snapshot["period"]["starts_at"] == observation.period.starts_at
            and snapshot["period"]["ends_at"] == observation.period.ends_at
            and snapshot["period"]["granularity"] == observation.period.granularity
            and observation.evidence_refs == (row["evidence_id"],)
            and hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
            == row["content_hash"]
        )
