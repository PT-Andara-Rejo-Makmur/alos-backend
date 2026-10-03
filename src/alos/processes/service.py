"""Transactional process actions with server routing, provenance and scope isolation."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import insert, literal, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from alos.audit import AuditEvent
from alos.domains.record_repository import RecordRepository, authorize, conflict
from alos.identity import Principal
from alos.notifications.business import enqueue_notice
from alos.observability.correlation import current_correlation_id
from alos.processes.authority import assigned, revalidate
from alos.processes.execution import verify_execution
from alos.processes.policy import requirements
from alos.processes.subjects import SUBJECTS, owner_domain, snapshot
from alos.security.errors import PlatformError


class ProcessService:
    def __init__(self, repository: RecordRepository) -> None:
        self.repository = repository

    async def table(self, session: AsyncSession, name: str) -> Any:
        return await self.repository.table(session, "core", "business_process" + name)

    @staticmethod
    def company(table: Any, principal: Principal) -> tuple[Any, ...]:
        return (
            table.c.tenant_id == principal.tenant_id,
            table.c.organization_id == principal.organization_id,
        )

    async def policy(
        self, session: AsyncSession, principal: Principal, business_type: str
    ) -> dict[str, Any]:
        policies = await self.table(session, "_policies")
        row = (
            (
                await session.execute(
                    select(policies)
                    .where(
                        *self.repository.scope(policies, principal),
                        policies.c.business_type == business_type,
                        policies.c.active.is_(True),
                    )
                    .with_for_update(read=True)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise PlatformError(
                "PROCESS_POLICY_UNAVAILABLE",
                "Aturan perusahaan belum dikonfigurasi.",
                status_code=409,
            )
        return dict(row)

    async def configure(self, principal: Principal, values: dict[str, Any]) -> dict[str, Any]:
        if "IT_ADMIN" not in principal.roles or "it.write" not in principal.permissions:
            raise PlatformError(
                "PROCESS_POLICY_DENIED", "Kewenangan konfigurasi diperlukan.", status_code=403
            )
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            workspaces = await self.repository.table(session, "core", "workspaces")
            owner = values["owning_workspace_id"]
            route_ids = set(values["routes"].values()) | {owner}
            route_ids.update(
                route["workspace_id"]
                for route in values["rules"].get("escalation_routes", {}).values()
            )
            visible = set(
                (
                    await session.scalars(
                        select(workspaces.c.workspace_id).where(
                            *self.company(workspaces, principal),
                            workspaces.c.workspace_id.in_(route_ids),
                            workspaces.c.active.is_(True),
                        )
                    )
                ).all()
            )
            if visible != route_ids:
                raise conflict("Divisi tujuan harus berada dalam perusahaan yang sama dan aktif.")
            table = await self.table(session, "_policies")
            # Serialize policy upserts even when no previous policy exists.
            await session.execute(
                select(workspaces).where(workspaces.c.workspace_id == owner).with_for_update()
            )
            old = (
                (
                    await session.execute(
                        select(table)
                        .where(
                            *self.company(table, principal),
                            table.c.workspace_id == owner,
                            table.c.business_type == values["business_type"],
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            policy_id = old["policy_id"] if old else uuid4().hex
            record = {
                "tenant_id": principal.tenant_id,
                "organization_id": principal.organization_id,
                "workspace_id": owner,
                "business_type": values["business_type"],
                "version": old["version"] + 1 if old else 1,
                "routes": values["routes"],
                "rules": values["rules"],
                "active": True,
                "updated_at": datetime.now(UTC),
            }
            if old:
                await session.execute(
                    update(table).where(table.c.policy_id == policy_id).values(**record)
                )
            else:
                await session.execute(insert(table).values(policy_id=policy_id, **record))
            await self.repository.audit.append_in_session(
                session,
                AuditEvent(
                    event_type="business_process.policy_configured",
                    entity_type="business_process_policy",
                    entity_id=policy_id,
                    tenant_id=principal.tenant_id,
                    organization_id=principal.organization_id,
                    workspace_id=principal.workspace_id,
                    actor_id=principal.actor_id,
                    correlation_id=current_correlation_id(),
                    outcome="SUCCEEDED",
                    occurred_at=datetime.now(UTC),
                    reason=values["reason"],
                    metadata={"version": record["version"], "roles": sorted(principal.roles)},
                ),
            )
            return dict(jsonable_encoder({"policy_id": policy_id, **record}))

    async def start(self, principal: Principal, values: dict[str, Any]) -> dict[str, Any]:
        business_type, subject_id = values["business_type"], values["subject_id"]
        if business_type == "OFFBOARDING" and "DIVISION_LEAD" not in principal.roles:
            raise PlatformError(
                "PROCESS_START_DENIED",
                "Kepala divisi harus memulai pengakhiran kerja.",
                status_code=403,
            )
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            # Policy then subject then process locks are also used by act/guard.
            policy = await self.policy(session, principal, business_type)
            packet, digest = await snapshot(
                self.repository, session, principal, business_type, subject_id, writing=True
            )
            processes = await self.table(session, "es")
            existing = (
                (
                    await session.execute(
                        select(processes)
                        .where(
                            *self.repository.scope(processes, principal),
                            processes.c.business_type == business_type,
                            processes.c.subject_id == subject_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if existing:
                if existing["subject_snapshot"] != digest:
                    raise conflict("Catatan berubah; kembalikan alur untuk pemeriksaan ulang.")
                return await self.project(session, dict(existing), principal)
            plan = requirements(business_type, packet, policy["rules"])
            now, process_id = datetime.now(UTC), uuid4().hex
            record = {
                "process_id": process_id,
                "tenant_id": principal.tenant_id,
                "organization_id": principal.organization_id,
                "workspace_id": principal.workspace_id,
                "business_type": business_type,
                "subject_id": subject_id,
                "requested_by": principal.actor_id,
                "policy_id": policy["policy_id"],
                "policy_version": policy["version"],
                "revision": 1,
                "subject_snapshot": digest,
                "packet": packet,
                "status": "READY",
                "direction_reason": None,
                "correlation_id": current_correlation_id(),
                "created_at": now,
                "updated_at": now,
            }
            await session.execute(insert(processes).values(**record))
            steps = await self.table(session, "_steps")
            for position, step in enumerate(plan):
                target = policy["routes"].get(step.domain)
                if target is None:
                    raise conflict("Aturan belum menentukan divisi yang bertanggung jawab.")
                due_hours = policy["rules"].get("due_hours", {}).get(step.code)
                await session.execute(
                    insert(steps).values(
                        step_id=uuid4().hex,
                        process_id=process_id,
                        position=position,
                        revision=1,
                        code=step.code,
                        kind=step.kind,
                        workspace_id=target,
                        role=step.role,
                        permission=step.permission,
                        instruction=step.instruction,
                        reason=step.reason,
                        independent=step.independent,
                        status="READY" if position == 0 else "PENDING",
                        due_at=now + timedelta(hours=due_hours)
                        if position == 0 and due_hours
                        else None,
                    )
                )
            await self.history(session, record, principal, "CREATED", values["reason"], [])
            return await self.project(session, record, principal)

    async def load(
        self, session: AsyncSession, principal: Principal, process_id: str
    ) -> dict[str, Any]:
        table = await self.table(session, "es")
        row = (
            (
                await session.execute(
                    select(table).where(
                        *self.company(table, principal), table.c.process_id == process_id
                    )
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise PlatformError("PROCESS_NOT_FOUND", "Alur proses tidak tersedia.", status_code=404)
        record = dict(row)
        steps = await self.steps(session, process_id)
        domain_permission = owner_domain(record["business_type"])
        owner = (
            record["workspace_id"] == principal.workspace_id
            and f"{domain_permission}.read" in principal.permissions
        )
        if not owner and not any(assigned(step, principal) for step in steps):
            raise PlatformError("PROCESS_NOT_FOUND", "Alur proses tidak tersedia.", status_code=404)
        return record

    async def steps(self, session: AsyncSession, process_id: str) -> list[dict[str, Any]]:
        table = await self.table(session, "_steps")
        processes = await self.table(session, "es")
        return [
            dict(row)
            for row in (
                await session.execute(
                    select(table)
                    .join(processes)
                    .where(
                        table.c.process_id == process_id,
                        table.c.revision == processes.c.revision,
                    )
                    .order_by(table.c.position)
                )
            )
            .mappings()
            .all()
        ]

    async def detail(self, principal: Principal, process_id: str) -> dict[str, Any]:
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            return await self.project(
                session, await self.load(session, principal, process_id), principal
            )

    async def for_subject(
        self, principal: Principal, business_type: str, subject_id: str
    ) -> dict[str, Any]:
        if business_type not in SUBJECTS:
            raise conflict("Jenis pengajuan tidak tersedia.")
        domain, spec, _ = SUBJECTS[business_type]
        authorize(principal, owner_domain(business_type), "read")
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            await self.repository.row(session, domain, spec, principal, subject_id)
            table = await self.table(session, "es")
            row = (
                (
                    await session.execute(
                        select(table).where(
                            *self.repository.scope(table, principal),
                            table.c.business_type == business_type,
                            table.c.subject_id == subject_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise PlatformError("PROCESS_NOT_FOUND", "Pengajuan belum dibuat.", status_code=404)
            return await self.project(session, dict(row), principal)

    async def act(
        self, principal: Principal, process_id: str, step_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            record = await self.load(session, principal, process_id)
            owner = replace(principal, workspace_id=record["workspace_id"])
            policy = await self.policy(session, owner, record["business_type"])
            _, digest = await snapshot(
                self.repository, session, owner, record["business_type"], record["subject_id"]
            )
            table = await self.table(session, "es")
            record = dict(
                (
                    await session.execute(
                        select(table).where(table.c.process_id == process_id).with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if (
                policy["version"] != record["policy_version"]
                or digest != record["subject_snapshot"]
                or values["subject_snapshot"] != digest
            ):
                raise conflict("Catatan atau aturan berubah; pemeriksaan harus diulang.")
            steps = await self.steps(session, process_id)
            step = next((item for item in steps if item["step_id"] == step_id), None)
            if step is None or not assigned(step, principal):
                raise PlatformError(
                    "PROCESS_ACTION_DENIED",
                    "Tindakan berada di luar kewenangan aktif.",
                    status_code=403,
                )
            if record["status"] not in {"READY", "IN_PROGRESS"} or step["status"] not in {
                "READY",
                "IN_PROGRESS",
            }:
                raise conflict("Langkah proses sudah berubah atau belum siap.")
            if step["independent"] and (
                record["requested_by"] == principal.actor_id
                or any(item["actor_id"] == principal.actor_id for item in steps)
            ):
                raise PlatformError(
                    "PROCESS_INDEPENDENCE_DENIED",
                    "Pemeriksa independen diperlukan.",
                    status_code=403,
                )
            evidence = values.get("evidence_refs", [])
            await self.validate_evidence(session, principal, evidence)
            action = values["action"]
            if action == "ACKNOWLEDGE" and step["kind"] != "ACKNOWLEDGEMENT":
                raise conflict("Langkah ini memerlukan pemeriksaan atau pelaksanaan.")
            if action == "COMPLETE" and step["kind"] == "ACKNOWLEDGEMENT":
                raise conflict("Konfirmasi untuk diketahui diperlukan.")
            status = {
                "START": "IN_PROGRESS",
                "COMPLETE": "COMPLETED",
                "ACKNOWLEDGE": "COMPLETED",
                "RETURN": "RETURNED",
            }[action]
            if status == "COMPLETED" and step.get("task_id"):
                tasks = await self.repository.table(session, "core", "tasks")
                task_status = await session.scalar(
                    select(tasks.c.status)
                    .where(
                        tasks.c.task_id == step["task_id"],
                        tasks.c.tenant_id == principal.tenant_id,
                        tasks.c.organization_id == principal.organization_id,
                    )
                    .with_for_update(read=True)
                )
                if task_status != "COMPLETED":
                    raise conflict("Tugas pelaksanaan yang terkait belum selesai.")
            if (step["kind"] == "EXECUTION" and status == "COMPLETED") or (
                step["code"] == "ASSET_RETURN" and status == "COMPLETED"
            ):
                await verify_execution(self.repository, session, record, step, policy)
            if step["code"] == "LEGAL_REVIEW" and status == "COMPLETED":
                contracts = record["packet"].get("contracts", [])
                if record["business_type"] == "CHANGE_ORDER" and not contracts:
                    raise conflict(
                        "Kontrak Legal terkait harus disertakan untuk perubahan kontrak."
                    )
                if any(item["status"] not in {"REVIEWED", "ACTIVE"} for item in contracts):
                    raise conflict("Pemeriksaan kontrak Legal belum selesai.")
            steps_table = await self.table(session, "_steps")
            await session.execute(
                update(steps_table)
                .where(steps_table.c.step_id == step_id)
                .values(
                    status=status,
                    actor_id=principal.actor_id,
                    completed_at=datetime.now(UTC) if status == "COMPLETED" else None,
                )
            )
            process_status = "RETURNED" if status == "RETURNED" else "IN_PROGRESS"
            if status == "COMPLETED":
                following = next(
                    (item for item in steps if item["position"] == step["position"] + 1), None
                )
                if following:
                    hours = policy["rules"].get("due_hours", {}).get(following["code"])
                    await session.execute(
                        update(steps_table)
                        .where(steps_table.c.step_id == following["step_id"])
                        .values(
                            status="READY",
                            due_at=datetime.now(UTC) + timedelta(hours=hours) if hours else None,
                        )
                    )
                else:
                    process_status = "COMPLETED"
            await session.execute(
                update(table)
                .where(table.c.process_id == process_id)
                .values(status=process_status, updated_at=datetime.now(UTC))
            )
            record["status"] = process_status
            record["updated_at"] = datetime.now(UTC)
            await self.history(
                session, record, principal, action, values["reason"], evidence, step_id
            )
            return await self.project(session, record, principal)

    async def revise(
        self, principal: Principal, process_id: str, reason: str, *, cancel: bool = False
    ) -> dict[str, Any]:
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            record = await self.load(session, principal, process_id)
            domain = owner_domain(record["business_type"])
            if (
                record["workspace_id"] != principal.workspace_id
                or f"{domain}.write" not in principal.permissions
                or (
                    record["requested_by"] != principal.actor_id
                    and "DIVISION_LEAD" not in principal.roles
                )
            ):
                raise PlatformError(
                    "PROCESS_REVISION_DENIED",
                    "Penanggung jawab pengajuan diperlukan.",
                    status_code=403,
                )
            policy = await self.policy(session, principal, record["business_type"])
            packet, digest = await snapshot(
                self.repository,
                session,
                principal,
                record["business_type"],
                record["subject_id"],
                writing=True,
            )
            table = await self.table(session, "es")
            record = dict(
                (
                    await session.execute(
                        select(table).where(table.c.process_id == process_id).with_for_update()
                    )
                )
                .mappings()
                .one()
            )
            if record["status"] == "CANCELLED":
                raise conflict("Alur yang dibatalkan tidak dapat diajukan ulang.")
            steps = await self.steps(session, process_id)
            if any(
                step["code"] == "IT_REVOCATION" and step["status"] == "COMPLETED" for step in steps
            ):
                raise conflict("Pencabutan akses selesai; lanjutkan pengakhiran kerja.")
            changes: dict[str, Any] = {
                "status": "CANCELLED" if cancel else "READY",
                "updated_at": datetime.now(UTC),
            }
            if not cancel:
                changes.update(
                    revision=record["revision"] + 1,
                    policy_version=policy["version"],
                    packet=packet,
                    subject_snapshot=digest,
                )
                steps_table = await self.table(session, "_steps")
                now = datetime.now(UTC)
                for position, step in enumerate(
                    requirements(
                        record["business_type"],
                        packet,
                        {
                            **policy["rules"],
                            **(
                                {"executive_required": True}
                                if record.get("direction_reason")
                                else {}
                            ),
                        },
                    )
                ):
                    target = policy["routes"].get(step.domain)
                    if not target:
                        raise conflict("Divisi penanggung jawab belum dikonfigurasi.")
                    hours = policy["rules"].get("due_hours", {}).get(step.code)
                    await session.execute(
                        insert(steps_table).values(
                            step_id=uuid4().hex,
                            process_id=process_id,
                            revision=changes["revision"],
                            position=position,
                            code=step.code,
                            kind=step.kind,
                            workspace_id=target,
                            role=step.role,
                            permission=step.permission,
                            instruction=step.instruction,
                            reason=step.reason,
                            independent=step.independent,
                            status="READY" if position == 0 else "PENDING",
                            due_at=now + timedelta(hours=hours)
                            if position == 0 and hours
                            else None,
                        )
                    )
            await session.execute(
                update(table).where(table.c.process_id == process_id).values(**changes)
            )
            record.update(changes)
            await self.history(
                session, record, principal, "CANCELLED" if cancel else "RESUBMITTED", reason, []
            )
            return await self.project(session, record, principal)

    async def validate_evidence(
        self, session: AsyncSession, principal: Principal, refs: list[str]
    ) -> None:
        if not refs:
            return
        table = await self.repository.table(session, "evidence", "evidence_refs")
        found = set(
            (
                await session.scalars(
                    select(table.c.evidence_id).where(
                        *self.repository.scope(table, principal), table.c.evidence_id.in_(refs)
                    )
                )
            ).all()
        )
        if found != set(refs):
            raise PlatformError(
                "PROCESS_EVIDENCE_DENIED", "Bukti tidak dapat diakses.", status_code=404
            )

    async def history(
        self,
        session: AsyncSession,
        record: dict[str, Any],
        principal: Principal,
        action: str,
        reason: str,
        evidence: list[str],
        step_id: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        table = await self.table(session, "_history")
        await session.execute(
            insert(table).values(
                history_id=uuid4().hex,
                process_id=record["process_id"],
                step_id=step_id,
                actor_id=principal.actor_id,
                workspace_id=principal.workspace_id,
                role_refs=sorted(principal.roles),
                permission_refs=sorted(principal.permissions),
                action=action,
                reason=reason,
                evidence_refs=evidence,
                correlation_id=current_correlation_id(),
                occurred_at=now,
            )
        )
        await self.repository.audit.append_in_session(
            session,
            AuditEvent(
                event_type="business_process." + action.lower(),
                entity_type="business_process",
                entity_id=record["process_id"],
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=current_correlation_id(),
                outcome="SUCCEEDED",
                occurred_at=now,
                reason=reason,
                metadata={
                    "step_id": step_id,
                    "roles": sorted(principal.roles),
                    "permissions": sorted(principal.permissions),
                    "evidence_refs": evidence,
                },
            ),
        )
        steps = await self.steps(session, record["process_id"])
        ready = next((item for item in steps if item["status"] == "READY"), None)
        if (
            action in {"CREATED", "RESUBMITTED", "COMPLETE", "ACKNOWLEDGE", "DIRECTION_REQUESTED"}
            and ready
        ):
            event = {
                "REVIEW": "REVIEW_REQUESTED",
                "DECISION": "DECISION_REQUIRED",
                "EXECUTION": "ASSIGNED",
                "ACKNOWLEDGEMENT": "ACKNOWLEDGEMENT",
            }[ready["kind"]]
            await enqueue_notice(self.repository, session, record, ready, event)
        if action == "RETURN":
            await enqueue_notice(self.repository, session, record, None, "RETURNED")
        elif record["status"] == "COMPLETED":
            await enqueue_notice(self.repository, session, record, None, "PROCESS_COMPLETED")
        if action == "COMPLETE" and any(
            item["step_id"] == step_id and item["kind"] == "DECISION" for item in steps
        ):
            await enqueue_notice(self.repository, session, record, None, "DECISION_COMPLETED")

    async def project(
        self, session: AsyncSession, record: dict[str, Any], principal: Principal
    ) -> dict[str, Any]:
        steps = await self.steps(session, record["process_id"])
        current = next(
            (step for step in steps if step["status"] in {"READY", "IN_PROGRESS", "RETURNED"}), None
        )
        history = await self.table(session, "_history")
        events = (
            (
                await session.execute(
                    select(
                        history.c.history_id,
                        history.c.step_id,
                        history.c.actor_id,
                        history.c.workspace_id,
                        history.c.role_refs,
                        history.c.action,
                        history.c.reason,
                        history.c.occurred_at,
                    )
                    .where(history.c.process_id == record["process_id"])
                    .order_by(history.c.occurred_at)
                )
            )
            .mappings()
            .all()
        )
        workspaces = await self.repository.table(session, "core", "workspaces")
        actors = await self.repository.table(session, "core", "actors")
        people: list[dict[str, Any]] = [*steps, *[dict(event) for event in events]]
        workspace_ids = {item["workspace_id"] for item in people} | {record["workspace_id"]}
        actor_ids = {item["actor_id"] for item in people if item["actor_id"]}
        workspace_names: dict[str, str] = {
            str(row[0]): str(row[1])
            for row in (
                await session.execute(
                    select(workspaces.c.workspace_id, workspaces.c.name).where(
                        *self.company(workspaces, principal),
                        workspaces.c.workspace_id.in_(workspace_ids),
                    )
                )
            ).all()
        }
        actor_names: dict[str, str] = {
            str(row[0]): str(row[1])
            for row in (
                await session.execute(
                    select(actors.c.actor_id, actors.c.display_name).where(
                        *self.company(actors, principal), actors.c.actor_id.in_(actor_ids)
                    )
                )
            ).all()
        }
        can_create_payable = (
            record["business_type"] == "PAYMENT_CERTIFICATE"
            and record["status"] == "COMPLETED"
            and any(
                item["code"] == "FINANCE_REVIEW" and assigned(item, principal) for item in steps
            )
        )
        if can_create_payable:
            certificates = await self.repository.table(session, "property", "payment_certificates")
            can_create_payable = (
                await session.scalar(
                    select(certificates.c.payment_certificate_id).where(
                        *self.company(certificates, principal),
                        certificates.c.workspace_id == record["workspace_id"],
                        certificates.c.payment_certificate_id == record["subject_id"],
                        certificates.c.status == "APPROVED",
                    )
                )
                is not None
            )
        return dict(
            jsonable_encoder(
                {
                    **record,
                    "steps": [
                        {
                            **step,
                            "workspace_name": workspace_names.get(step["workspace_id"]),
                            "actor_name": actor_names.get(step["actor_id"]),
                            "can_create_task": assigned(step, principal)
                            and "work.write" in principal.permissions
                            and record["status"] in {"READY", "IN_PROGRESS"}
                            and step["kind"] == "EXECUTION"
                            and step["task_id"] is None
                            and step["status"] in {"READY", "IN_PROGRESS"},
                            "can_act": assigned(step, principal)
                            and record["status"] in {"READY", "IN_PROGRESS"}
                            and step["status"] in {"READY", "IN_PROGRESS"},
                        }
                        for step in steps
                    ],
                    "next_action": "Perbaiki pengajuan dan ajukan pemeriksaan ulang"
                    if record["status"] == "RETURNED"
                    else current["instruction"]
                    if current
                    else None,
                    "can_resubmit": record["status"] == "RETURNED"
                    and record["workspace_id"] == principal.workspace_id
                    and f"{owner_domain(record['business_type'])}.write" in principal.permissions
                    and (
                        record["requested_by"] == principal.actor_id
                        or "DIVISION_LEAD" in principal.roles
                    ),
                    "can_request_direction": record["status"]
                    in {"READY", "IN_PROGRESS", "COMPLETED"}
                    and record["workspace_id"] == principal.workspace_id
                    and "DIVISION_LEAD" in principal.roles
                    and f"{owner_domain(record['business_type'])}.write" in principal.permissions
                    and not record.get("direction_reason")
                    and not any(
                        item["kind"] == "DECISION" and item["role"] == "EXECUTIVE" for item in steps
                    ),
                    "can_attach_contract": record["business_type"]
                    in {"CHANGE_ORDER", "EMPLOYMENT_CONTRACT"}
                    and record["status"] in {"READY", "IN_PROGRESS", "RETURNED"}
                    and any(
                        item["code"] == "LEGAL_REVIEW" and assigned(item, principal)
                        for item in steps
                    ),
                    "can_create_payable": can_create_payable,
                    "responsible_workspace_name": workspace_names.get(
                        record["workspace_id"]
                        if record["status"] == "RETURNED"
                        else current["workspace_id"]
                        if current
                        else ""
                    ),
                    "responsible_workspace_id": record["workspace_id"]
                    if record["status"] == "RETURNED"
                    else current["workspace_id"]
                    if current
                    else None,
                    "responsible_role": current["role"] if current else None,
                    "due_at": current["due_at"] if current else None,
                    "history": [
                        {
                            **event,
                            "workspace_name": workspace_names.get(event["workspace_id"]),
                            "actor_name": actor_names.get(event["actor_id"]),
                        }
                        for event in events
                    ],
                }
            )
        )

    async def queue(self, principal: Principal) -> dict[str, Any]:
        async with self.repository.factory() as session, session.begin():
            await revalidate(self.repository, session, principal)
            table, steps = await self.table(session, "es"), await self.table(session, "_steps")
            rows = (
                (
                    await session.execute(
                        select(table)
                        .join(steps)
                        .where(
                            *self.company(table, principal),
                            steps.c.workspace_id == principal.workspace_id,
                            steps.c.role.in_(principal.roles),
                            steps.c.permission.in_(principal.permissions),
                            steps.c.status.in_(("READY", "IN_PROGRESS")),
                            steps.c.revision == table.c.revision,
                            table.c.status.in_(("READY", "IN_PROGRESS")),
                        )
                        .order_by(steps.c.due_at.asc().nulls_last(), table.c.created_at)
                        .limit(200)
                    )
                )
                .mappings()
                .all()
            )
            owner_types = [
                kind
                for kind, (domain, _, _) in SUBJECTS.items()
                if f"{owner_domain(kind)}.write" in principal.permissions
            ]
            if owner_types:
                returned = (
                    (
                        await session.execute(
                            select(table)
                            .where(
                                *self.repository.scope(table, principal),
                                table.c.business_type.in_(owner_types),
                                table.c.status == "RETURNED",
                                or_(
                                    table.c.requested_by == principal.actor_id,
                                    literal("DIVISION_LEAD" in principal.roles),
                                ),
                            )
                            .order_by(table.c.updated_at.desc())
                            .limit(200)
                        )
                    )
                    .mappings()
                    .all()
                )
                rows = [*returned, *rows]
            return {"items": [await self.project(session, dict(row), principal) for row in rows]}
