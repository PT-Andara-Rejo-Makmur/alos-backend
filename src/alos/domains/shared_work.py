"""PostgreSQL authority for workspace-visible projects and tasks."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, ClassVar, cast
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import (
    MetaData,
    Table,
    and_,
    delete,
    exists,
    func,
    insert,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.audit import AuditEvent, SqlAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.governance.material_approvals import BusinessApprovalSubjectPort
from alos.identity import DataScope, Principal
from alos.observability.correlation import current_correlation_id
from alos.security.errors import PlatformError

if TYPE_CHECKING:
    from executive_contracts import ExecutiveSharedWorkSummary


def calculate_project_progress(tasks: Sequence[dict[str, Any] | Any]) -> int:
    """Canonical project progress: completed active tasks / all active non-cancelled tasks."""
    active_tasks = [item for item in tasks if item["status"] != "CANCELLED"]
    if not active_tasks:
        return 0
    completed = sum(item["status"] == "COMPLETED" for item in active_tasks)
    return round(100 * completed / len(active_tasks))


class SharedWorkService:
    _approval_subjects: ClassVar[dict[str, tuple[str, str, str]]] = {
        "PROJECT": ("projects", "project_workspaces", "project_id"),
        "TASK": ("tasks", "task_workspaces", "task_id"),
    }

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._metadata = MetaData()
        self._tables: dict[str, Table] = {}
        self._reflection_lock = asyncio.Lock()
        self._material_ports: dict[str, BusinessApprovalSubjectPort] = {}
        self._contracts: CanonicalContractCatalog | None = None
        self._audit = SqlAuditRepository(session_factory)

    def configure_material_approvals(
        self,
        contracts: CanonicalContractCatalog,
        ports: dict[str, BusinessApprovalSubjectPort],
    ) -> None:
        self._contracts = contracts
        self._material_ports = ports

    @property
    def approval_subject_types(self) -> tuple[str, ...]:
        if self._contracts is None:
            return tuple(self._approval_subjects)
        return tuple(
            self._contracts.enum_values(
                "https://schemas.alos.dev/v1/shared-work/shared-work.schema.json",
                "ApprovalSubjectType",
            )
        )

    async def _material_subject(
        self,
        session: AsyncSession,
        principal: Principal,
        row: dict[str, Any],
        *,
        mode: str,
    ) -> dict[str, Any]:
        port = self._material_ports.get(str(row["subject_type"]).split("_", 1)[0])
        if port is None:
            raise self._not_found()
        return await port.approval_subject(
            session,
            principal,
            row["subject_type"],
            row["subject_id"],
            row.get("requested_action"),
            mode=mode,
        )

    async def _audit_material_approval(
        self,
        session: AsyncSession,
        principal: Principal,
        row: dict[str, Any],
        operation: str,
    ) -> None:
        if self._contracts is None:
            raise PlatformError(
                "CONTRACTS_UNAVAILABLE", "Canonical contracts are required.", status_code=503
            )
        self._contracts.validate(
            "https://schemas.alos.dev/v1/shared-work/shared-work.schema.json#/$defs/ApprovalProjection",
            self._projection(row, principal),
        )
        await self._audit.append_in_session(
            session,
            AuditEvent(
                event_type=f"approval.{operation}",
                entity_type="approval",
                entity_id=row["approval_id"],
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=current_correlation_id(),
                outcome="SUCCEEDED",
                occurred_at=datetime.now(UTC),
                reason="Action-scoped business approval",
                metadata={
                    "requested_action": row["requested_action"],
                    "transition_ref": row.get("transition_ref"),
                },
            ),
        )

    async def consume_in_session(
        self,
        session: AsyncSession,
        principal: Principal,
        approval_id: str,
        subject_type: str,
        subject_id: str,
        requested_action: str,
    ) -> None:
        approvals = await self._table(session, "work_approvals")
        links = await self._table(session, "work_approval_workspaces")
        row = await self._locked_record(
            session, approvals, links, "approval_id", approval_id, principal
        )
        if (
            row["subject_type"] != subject_type
            or row["subject_id"] != subject_id
            or row["requested_action"] != requested_action
            or row["status"] != "APPROVED"
            or row["decision"] != "APPROVED"
            or row["consumed_at"] is not None
            or not row["approver_actor_id"]
            or row["requested_by"] == row["approver_actor_id"]
        ):
            raise PlatformError(
                "MATERIAL_APPROVAL_CONFLICT",
                "Approval does not authorize this action or was consumed.",
                status_code=409,
            )
        subject = await self._material_subject(session, principal, row, mode="execute")
        if subject["snapshot"] != row["subject_snapshot"]:
            raise PlatformError(
                "MATERIAL_APPROVAL_STALE",
                "Business content changed after this request.",
                status_code=409,
            )
        updated = dict(
            (
                await session.execute(
                    update(approvals)
                    .where(approvals.c.approval_id == approval_id)
                    .values(
                        consumed_at=datetime.now(UTC),
                        consumed_by=principal.actor_id,
                        transition_ref=uuid4().hex,
                    )
                    .returning(approvals)
                )
            )
            .mappings()
            .one()
        )
        await self._audit_material_approval(session, principal, updated, "consumed")

    async def _table(self, session: AsyncSession, name: str, schema: str = "core") -> Table:
        key = f"{schema}.{name}"
        cached = self._tables.get(key)
        if cached is not None:
            return cached
        async with self._reflection_lock:
            cached = self._tables.get(key)
            if cached is not None:
                return cached
            connection = await session.connection()
            table = await connection.run_sync(
                lambda sync_connection: Table(
                    name, self._metadata, schema=schema, autoload_with=sync_connection
                )
            )
            self._tables[key] = table
            return table

    async def list_workspace_members(self, principal: Principal) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await self._verify_workspace(session, principal)
            actors = await self._table(session, "actors")
            memberships = await self._table(session, "workspace_memberships")
            workspaces = await self._table(session, "workspaces")
            accounts = await self._table(session, "auth_accounts")
            employees = await self._table(session, "employees", schema="hr")
            now = datetime.now(UTC)
            account_exists = exists(
                select(1)
                .select_from(accounts)
                .where(
                    accounts.c.actor_id == actors.c.actor_id,
                    accounts.c.tenant_id == principal.tenant_id,
                    accounts.c.organization_id == principal.organization_id,
                    accounts.c.active.is_(True),
                    accounts.c.administrative_state == "ENABLED",
                    accounts.c.activation_state == "ACTIVATED",
                )
            )
            query = (
                select(
                    actors.c.actor_id,
                    actors.c.display_name,
                    memberships.c.roles.label("role_refs"),
                    memberships.c.permission_refs,
                    workspaces.c.division_code,
                    employees.c.employee_id,
                    employees.c.employee_number,
                    employees.c.position_title,
                    employees.c.department_code,
                )
                .select_from(actors)
                .join(memberships, memberships.c.actor_id == actors.c.actor_id)
                .join(workspaces, workspaces.c.workspace_id == memberships.c.workspace_id)
                .outerjoin(
                    employees,
                    and_(
                        employees.c.actor_id == actors.c.actor_id,
                        employees.c.tenant_id == principal.tenant_id,
                        employees.c.organization_id == principal.organization_id,
                        employees.c.workspace_id == principal.workspace_id,
                        employees.c.employment_status == "ACTIVE",
                    ),
                )
                .where(
                    actors.c.tenant_id == principal.tenant_id,
                    actors.c.organization_id == principal.organization_id,
                    actors.c.active.is_(True),
                    memberships.c.tenant_id == principal.tenant_id,
                    memberships.c.organization_id == principal.organization_id,
                    memberships.c.workspace_id == principal.workspace_id,
                    memberships.c.active.is_(True),
                    memberships.c.revoked_at.is_(None),
                    memberships.c.effective_at <= now,
                    or_(memberships.c.expires_at.is_(None), memberships.c.expires_at > now),
                    workspaces.c.tenant_id == principal.tenant_id,
                    workspaces.c.organization_id == principal.organization_id,
                    workspaces.c.active.is_(True),
                    account_exists,
                )
                .order_by(actors.c.display_name, actors.c.actor_id, employees.c.employee_id)
                .limit(500)
            )
            rows = (await session.execute(query)).mappings().all()
            directory: dict[str, dict[str, Any]] = {}
            for row in rows:
                member_id = str(row["actor_id"])
                if member_id in directory:
                    continue
                permission_refs = set(row["permission_refs"] or [])
                directory[member_id] = {
                    "actor_id": member_id,
                    "display_name": row["display_name"],
                    "employee_id": row["employee_id"],
                    "employee_number": row["employee_number"],
                    "position_title": row["position_title"],
                    "department_code": row["department_code"],
                    "division_code": row["division_code"],
                    "workspace_id": principal.workspace_id,
                    "role_refs": row["role_refs"],
                    "active": True,
                    "project_assignable": bool({"project.read", "work.read"} & permission_refs),
                    "task_assignable": bool({"task.read", "work.read"} & permission_refs),
                    "finding_assignable": bool({"finding.read", "work.read"} & permission_refs),
                }
            return list(directory.values())

    @staticmethod
    def _visible(
        table: Table, link: Table, identifier: str, principal: Principal
    ) -> tuple[Any, ...]:
        return (
            table.c.tenant_id == principal.tenant_id,
            table.c.organization_id == principal.organization_id,
            exists(
                select(1)
                .select_from(link)
                .where(
                    link.c[identifier] == table.c[identifier],
                    link.c.workspace_id == principal.workspace_id,
                )
            ),
        )

    @staticmethod
    def _projection(row: dict[str, Any], principal: Principal) -> dict[str, Any]:
        projection = {**jsonable_encoder(row), "workspace_ids": [principal.workspace_id]}
        if row.get("requested_action") is not None:
            domain = str(row["subject_type"]).split("_", 1)[0].lower()
            can_decide = (
                row.get("status") == "PENDING"
                and row.get("requested_by") != principal.actor_id
                and "DIVISION_LEAD" in principal.roles
                and f"{domain}.write" in principal.permissions
            )
            projection["allowed_decisions"] = [
                decision
                for decision, permission in (
                    ("APPROVED", "approval.approve"),
                    ("RETURNED", "approval.return"),
                    ("REJECTED", "approval.reject"),
                    ("HOLD", "approval.hold"),
                )
                if can_decide and permission in principal.permissions
            ]
        return projection

    async def present(
        self, principal: Principal, entity_type: str, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not rows:
            return rows
        async with self._session_factory() as session:
            actors = await self._table(session, "actors")
            workspaces = await self._table(session, "workspaces")
            actor_fields = {
                "PROJECT": ("owner_actor_id",),
                "TASK": ("owner_actor_id", "created_by"),
                "APPROVAL": ("requested_by", "approver_actor_id"),
                "DOCUMENT": ("owner_actor_id",),
                "REPORT": ("owner_actor_id",),
                "FINDING": ("owner_actor_id", "verifier_actor_id"),
            }[entity_type]
            actor_ids = {str(row[key]) for row in rows for key in actor_fields if row.get(key)}
            names: dict[str, str] = {}
            if actor_ids:
                names = {
                    str(item[0]): str(item[1])
                    for item in (
                        await session.execute(
                            select(actors.c.actor_id, actors.c.display_name).where(
                                actors.c.actor_id.in_(actor_ids),
                                actors.c.tenant_id == principal.tenant_id,
                                actors.c.organization_id == principal.organization_id,
                            )
                        )
                    ).all()
                }
            workspace_name = (
                await session.execute(
                    select(workspaces.c.name).where(
                        workspaces.c.workspace_id == principal.workspace_id,
                        workspaces.c.tenant_id == principal.tenant_id,
                        workspaces.c.organization_id == principal.organization_id,
                    )
                )
            ).scalar_one_or_none()
            identifiers = {
                "PROJECT": "project_id",
                "TASK": "task_id",
                "APPROVAL": "approval_id",
                "DOCUMENT": "document_id",
                "REPORT": "report_id",
                "FINDING": "finding_id",
            }
            ids = {str(row[identifiers[entity_type]]) for row in rows}
            evidence = await self._table(session, "shared_work_evidence_links")
            comments = await self._table(session, "shared_work_comments")
            evidence_counts: dict[str, int] = {
                str(item[0]): int(item[1])
                for item in (
                    await session.execute(
                        select(evidence.c.entity_id, func.count())
                        .where(
                            evidence.c.tenant_id == principal.tenant_id,
                            evidence.c.organization_id == principal.organization_id,
                            evidence.c.workspace_id == principal.workspace_id,
                            evidence.c.entity_type == entity_type,
                            evidence.c.entity_id.in_(ids),
                        )
                        .group_by(evidence.c.entity_id)
                    )
                ).all()
            }
            comment_counts: dict[str, int] = {
                str(item[0]): int(item[1])
                for item in (
                    await session.execute(
                        select(comments.c.entity_id, func.count())
                        .where(
                            comments.c.tenant_id == principal.tenant_id,
                            comments.c.organization_id == principal.organization_id,
                            comments.c.workspace_id == principal.workspace_id,
                            comments.c.entity_type == entity_type,
                            comments.c.entity_id.in_(ids),
                        )
                        .group_by(comments.c.entity_id)
                    )
                ).all()
            }
            document_link_counts: dict[tuple[str, str], int] = {}
            if entity_type in {"TASK", "APPROVAL", "DOCUMENT"}:
                document_links = await self._table(session, "shared_work_document_links")
                if entity_type == "DOCUMENT":
                    link_rows = (
                        await session.execute(
                            select(
                                document_links.c.document_id,
                                document_links.c.target_type,
                                func.count(),
                            )
                            .where(
                                document_links.c.tenant_id == principal.tenant_id,
                                document_links.c.organization_id == principal.organization_id,
                                document_links.c.workspace_id == principal.workspace_id,
                                document_links.c.document_id.in_(ids),
                            )
                            .group_by(document_links.c.document_id, document_links.c.target_type)
                        )
                    ).all()
                    document_link_counts = {
                        (str(document_id), str(target_type)): int(str(count))
                        for document_id, target_type, count in link_rows
                    }
                else:
                    link_rows = (
                        await session.execute(
                            select(document_links.c.target_id, func.count())
                            .where(
                                document_links.c.tenant_id == principal.tenant_id,
                                document_links.c.organization_id == principal.organization_id,
                                document_links.c.workspace_id == principal.workspace_id,
                                document_links.c.target_type == entity_type,
                                document_links.c.target_id.in_(ids),
                            )
                            .group_by(document_links.c.target_id)
                        )
                    ).all()
                    document_link_counts = {
                        (str(target_id), "DOCUMENT"): int(str(count))
                        for target_id, count in link_rows
                    }
            for row in rows:
                record_id = str(row[identifiers[entity_type]])
                row["workspace_name"] = workspace_name
                row["evidence_count"] = evidence_counts.get(record_id, 0)
                if entity_type in {"TASK", "APPROVAL", "REPORT"}:
                    row["comments_count"] = comment_counts.get(record_id, 0)
                if entity_type in {"PROJECT", "TASK", "DOCUMENT", "REPORT", "FINDING"}:
                    row["owner_name"] = names.get(str(row.get("owner_actor_id")))
                if entity_type == "TASK":
                    row["creator_name"] = names.get(str(row.get("created_by")))
                if entity_type == "APPROVAL":
                    row["requester_name"] = names.get(str(row.get("requested_by")))
                    row["approver_name"] = names.get(str(row.get("approver_actor_id")))
                if entity_type == "FINDING":
                    row["verifier_name"] = names.get(str(row.get("verifier_actor_id")))
                if entity_type in {"TASK", "APPROVAL"}:
                    row["documents_count"] = document_link_counts.get((record_id, "DOCUMENT"), 0)
                if entity_type == "DOCUMENT":
                    row["tasks_count"] = document_link_counts.get((record_id, "TASK"), 0)
                    row["approvals_count"] = document_link_counts.get((record_id, "APPROVAL"), 0)
            if entity_type == "PROJECT":
                tasks = await self._table(session, "tasks")
                task_links = await self._table(session, "task_workspaces")
                findings = await self._table(session, "work_findings")
                finding_links = await self._table(session, "work_finding_workspaces")
                documents = await self._table(session, "documents")
                reports = await self._table(session, "work_reports")
                report_links = await self._table(session, "work_report_workspaces")
                approvals = await self._table(session, "work_approvals")
                approval_links = await self._table(session, "work_approval_workspaces")
                task_rows = (
                    (
                        await session.execute(
                            select(
                                tasks.c.task_id,
                                tasks.c.project_id,
                                tasks.c.status,
                                tasks.c.priority,
                                tasks.c.due_at,
                            ).where(
                                tasks.c.project_id.in_(ids),
                                *self._visible(tasks, task_links, "task_id", principal),
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                finding_rows = (
                    (
                        await session.execute(
                            select(
                                findings.c.project_id, findings.c.status, findings.c.severity
                            ).where(
                                findings.c.project_id.in_(ids),
                                *self._visible(findings, finding_links, "finding_id", principal),
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                document_rows = (
                    await session.execute(
                        select(documents.c.project_id).where(
                            documents.c.project_id.in_(ids),
                            *self._document_scope(documents, principal),
                        )
                    )
                ).all()
                report_rows = (
                    await session.execute(
                        select(reports.c.project_id).where(
                            reports.c.project_id.in_(ids),
                            *self._visible(reports, report_links, "report_id", principal),
                        )
                    )
                ).all()
                task_projects = {
                    str(item["task_id"]): str(item["project_id"]) for item in task_rows
                }
                approval_rows = (
                    (
                        await session.execute(
                            select(approvals.c.subject_type, approvals.c.subject_id).where(
                                or_(
                                    and_(
                                        approvals.c.subject_type == "PROJECT",
                                        approvals.c.subject_id.in_(ids),
                                    ),
                                    and_(
                                        approvals.c.subject_type == "TASK",
                                        approvals.c.subject_id.in_(task_projects),
                                    ),
                                ),
                                *self._visible(approvals, approval_links, "approval_id", principal),
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                now = datetime.now(UTC)
                for row in rows:
                    project_id = str(row["project_id"])
                    project_tasks = [item for item in task_rows if item["project_id"] == project_id]
                    active_tasks = [item for item in project_tasks if item["status"] != "CANCELLED"]
                    project_findings = [
                        item for item in finding_rows if item["project_id"] == project_id
                    ]
                    row["tasks_count"] = len(project_tasks)
                    row["documents_count"] = sum(item[0] == project_id for item in document_rows)
                    row["reports_count"] = sum(item[0] == project_id for item in report_rows)
                    row["findings_count"] = len(project_findings)
                    row["approvals_count"] = sum(
                        (item["subject_type"] == "PROJECT" and item["subject_id"] == project_id)
                        or (
                            item["subject_type"] == "TASK"
                            and task_projects.get(str(item["subject_id"])) == project_id
                        )
                        for item in approval_rows
                    )
                    row["progress_percentage"] = calculate_project_progress(project_tasks)
                    open_findings = [
                        item for item in project_findings if item["status"] != "CLOSED"
                    ]
                    overdue = [
                        item
                        for item in active_tasks
                        if item["status"] != "COMPLETED" and item["due_at"] and item["due_at"] < now
                    ]
                    if any(item["severity"] == "CRITICAL" for item in open_findings) or any(
                        item["priority"] == "CRITICAL" for item in overdue
                    ):
                        row["risk_level"] = "CRITICAL"
                    elif any(item["severity"] == "HIGH" for item in open_findings) or any(
                        item["priority"] == "HIGH" for item in overdue
                    ):
                        row["risk_level"] = "HIGH"
                    elif row["status"] == "ON_HOLD" or any(
                        item["status"] == "BLOCKED" for item in active_tasks
                    ):
                        row["risk_level"] = "MEDIUM"
                    else:
                        row["risk_level"] = "LOW"
            if entity_type in {"TASK", "DOCUMENT", "FINDING"}:
                projects = await self._table(session, "projects")
                project_ids = {str(row["project_id"]) for row in rows if row.get("project_id")}
                project_names = dict()
                if project_ids:
                    project_names = {
                        str(item["project_id"]): item
                        for item in (
                            await session.execute(
                                select(
                                    projects.c.project_id, projects.c.name, projects.c.code
                                ).where(
                                    projects.c.project_id.in_(project_ids),
                                    projects.c.tenant_id == principal.tenant_id,
                                    projects.c.organization_id == principal.organization_id,
                                )
                            )
                        )
                        .mappings()
                        .all()
                    }
                for row in rows:
                    project = project_names.get(str(row.get("project_id")))
                    row["project_name"] = project["name"] if project else None
                    if entity_type in {"TASK", "DOCUMENT"}:
                        row["project_code"] = project["code"] if project else None
            if entity_type == "FINDING":
                tasks = await self._table(session, "tasks")
                task_ids = {
                    str(row["corrective_action_task_id"])
                    for row in rows
                    if row.get("corrective_action_task_id")
                }
                titles: dict[str, str] = (
                    {
                        str(item[0]): str(item[1])
                        for item in (
                            await session.execute(
                                select(tasks.c.task_id, tasks.c.title).where(
                                    tasks.c.task_id.in_(task_ids),
                                    tasks.c.tenant_id == principal.tenant_id,
                                    tasks.c.organization_id == principal.organization_id,
                                )
                            )
                        ).all()
                    }
                    if task_ids
                    else {}
                )
                for row in rows:
                    row["corrective_action_task_title"] = titles.get(
                        str(row.get("corrective_action_task_id"))
                    )
                    row["tasks_count"] = int(row.get("corrective_action_task_id") is not None)
            if entity_type == "APPROVAL":
                projects = await self._table(session, "projects")
                tasks = await self._table(session, "tasks")
                project_ids = {
                    str(row["subject_id"]) for row in rows if row["subject_type"] == "PROJECT"
                }
                task_ids = {str(row["subject_id"]) for row in rows if row["subject_type"] == "TASK"}
                project_titles: dict[str, str] = (
                    {
                        str(item[0]): str(item[1])
                        for item in (
                            await session.execute(
                                select(projects.c.project_id, projects.c.name).where(
                                    projects.c.project_id.in_(project_ids),
                                    projects.c.tenant_id == principal.tenant_id,
                                    projects.c.organization_id == principal.organization_id,
                                )
                            )
                        ).all()
                    }
                    if project_ids
                    else {}
                )
                task_titles: dict[str, str] = (
                    {
                        str(item[0]): str(item[1])
                        for item in (
                            await session.execute(
                                select(tasks.c.task_id, tasks.c.title).where(
                                    tasks.c.task_id.in_(task_ids),
                                    tasks.c.tenant_id == principal.tenant_id,
                                    tasks.c.organization_id == principal.organization_id,
                                )
                            )
                        ).all()
                    }
                    if task_ids
                    else {}
                )
                for row in rows:
                    row["subject_title"] = (
                        project_titles if row["subject_type"] == "PROJECT" else task_titles
                    ).get(str(row["subject_id"]))
            if entity_type == "DOCUMENT":
                versions = await self._table(session, "document_versions")
                version_rows = (
                    await session.execute(
                        select(versions.c.document_id, versions.c.version)
                        .where(
                            versions.c.document_id.in_(ids),
                            *self._document_scope(versions, principal),
                        )
                        .order_by(versions.c.created_at.desc(), versions.c.record_id.desc())
                    )
                ).all()
                current: dict[str, str] = {}
                for document_id, version in version_rows:
                    current.setdefault(str(document_id), str(version))
                for row in rows:
                    row["current_version"] = current.get(str(row["document_id"]))
            if entity_type == "TASK":
                findings = await self._table(session, "work_findings")
                finding_links = await self._table(session, "work_finding_workspaces")
                linked_tasks = (
                    await session.execute(
                        select(findings.c.corrective_action_task_id).where(
                            findings.c.corrective_action_task_id.in_(ids),
                            *self._visible(findings, finding_links, "finding_id", principal),
                        )
                    )
                ).all()
                finding_counts = Counter(item[0] for item in linked_tasks)
                dependencies = await self._table(session, "shared_work_task_dependencies")
                blockers = await self._table(session, "tasks")
                blocker_links = await self._table(session, "task_workspaces")
                dependency_rows = (
                    (
                        await session.execute(
                            select(
                                dependencies.c.task_id,
                                dependencies.c.blocked_by_task_id,
                                blockers.c.title,
                                blockers.c.status,
                                dependencies.c.linked_at,
                            )
                            .join(blockers, blockers.c.task_id == dependencies.c.blocked_by_task_id)
                            .where(
                                dependencies.c.tenant_id == principal.tenant_id,
                                dependencies.c.organization_id == principal.organization_id,
                                dependencies.c.workspace_id == principal.workspace_id,
                                dependencies.c.task_id.in_(ids),
                                *self._visible(blockers, blocker_links, "task_id", principal),
                            )
                            .order_by(dependencies.c.linked_at, dependencies.c.dependency_id)
                        )
                    )
                    .mappings()
                    .all()
                )
                by_task: dict[str, list[dict[str, Any]]] = {}
                for item in dependency_rows:
                    by_task.setdefault(str(item["task_id"]), []).append(
                        {
                            "blocked_by_task_id": str(item["blocked_by_task_id"]),
                            "title": item["title"],
                            "status": item["status"],
                            "linked_at": jsonable_encoder(item["linked_at"]),
                        }
                    )
                for row in rows:
                    row["findings_count"] = finding_counts[str(row["task_id"])]
                    row["blocked_by"] = by_task.get(str(row["task_id"]), [])
            return rows

    @staticmethod
    def _not_found() -> PlatformError:
        return PlatformError("WORK_RECORD_NOT_FOUND", "Work record was not found.", status_code=404)

    async def _locked_record(
        self,
        session: AsyncSession,
        table: Table,
        link: Table,
        identifier: str,
        record_id: str,
        principal: Principal,
    ) -> dict[str, Any]:
        row = (
            (
                await session.execute(
                    select(table)
                    .where(
                        table.c[identifier] == record_id,
                        *self._visible(table, link, identifier, principal),
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            raise self._not_found()
        return dict(row)

    async def _visible_project_id(
        self, session: AsyncSession, principal: Principal, project_id: str
    ) -> None:
        projects = await self._table(session, "projects")
        links = await self._table(session, "project_workspaces")
        visible = await session.execute(
            select(projects.c.project_id).where(
                projects.c.project_id == project_id,
                *self._visible(projects, links, "project_id", principal),
            )
        )
        if visible.scalar_one_or_none() is None:
            raise self._not_found()

    async def validate_project_reference(
        self, session: AsyncSession, principal: Principal, project_id: str
    ) -> None:
        """Owner boundary for scoped business references in the caller's transaction."""
        await self._visible_project_id(session, principal, project_id)

    async def visible_project_ids(
        self, session: AsyncSession, principal: Principal
    ) -> tuple[str, ...]:
        projects = await self._table(session, "projects")
        links = await self._table(session, "project_workspaces")
        result: ScalarResult[str] = await session.scalars(
            select(projects.c.project_id).where(
                *self._visible(projects, links, "project_id", principal)
            )
        )
        return tuple(result.all())

    async def validate_document_reference(
        self, session: AsyncSession, principal: Principal, document_id: str
    ) -> None:
        await self._document_row(session, principal, document_id)

    async def _visible_task_id(
        self, session: AsyncSession, principal: Principal, task_id: str
    ) -> None:
        tasks = await self._table(session, "tasks")
        links = await self._table(session, "task_workspaces")
        visible = await session.execute(
            select(tasks.c.task_id).where(
                tasks.c.task_id == task_id,
                *self._visible(tasks, links, "task_id", principal),
            )
        )
        if visible.scalar_one_or_none() is None:
            raise self._not_found()

    async def validate_document_version_reference(
        self,
        session: AsyncSession,
        principal: Principal,
        document_id: str,
        version: str,
    ) -> None:
        await self._document_row(session, principal, document_id)
        versions = await self._table(session, "document_versions")
        found = await session.scalar(
            select(versions.c.record_id).where(
                versions.c.document_id == document_id,
                versions.c.version == version,
                *self._document_scope(versions, principal),
            )
        )
        if found is None:
            raise self._not_found()

    async def _assert_shared_entity(
        self, session: AsyncSession, principal: Principal, entity_type: str, entity_id: str
    ) -> None:
        resources = {
            "PROJECT": ("projects", "project_workspaces", "project_id"),
            "TASK": ("tasks", "task_workspaces", "task_id"),
            "APPROVAL": ("work_approvals", "work_approval_workspaces", "approval_id"),
            "REPORT": ("work_reports", "work_report_workspaces", "report_id"),
            "FINDING": ("work_findings", "work_finding_workspaces", "finding_id"),
        }
        if entity_type == "DOCUMENT":
            await self._document_row(session, principal, entity_id)
            return
        configuration = resources.get(entity_type)
        if configuration is None:
            raise self._not_found()
        if entity_type == "APPROVAL":
            approvals = await self._table(session, "work_approvals")
            approval = (
                (
                    await session.execute(
                        select(approvals).where(
                            approvals.c.approval_id == entity_id,
                            approvals.c.tenant_id == principal.tenant_id,
                            approvals.c.organization_id == principal.organization_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if approval is not None and approval["requested_action"] is not None:
                await self._material_subject(session, principal, dict(approval), mode="read")
        table_name, link_name, identifier = configuration
        table = await self._table(session, table_name)
        link = await self._table(session, link_name)
        found = (
            await session.execute(
                select(table.c[identifier]).where(
                    table.c[identifier] == entity_id,
                    *self._visible(table, link, identifier, principal),
                )
            )
        ).scalar_one_or_none()
        if found is None:
            raise self._not_found()

    async def _relation_projection(
        self, session: AsyncSession, principal: Principal, entity_type: str, entity_id: str
    ) -> dict[str, str]:
        resources = {
            "PROJECT": ("projects", "project_workspaces", "project_id", "name"),
            "TASK": ("tasks", "task_workspaces", "task_id", "title"),
            "REPORT": ("work_reports", "work_report_workspaces", "report_id", "title"),
            "FINDING": ("work_findings", "work_finding_workspaces", "finding_id", "title"),
        }
        if entity_type == "DOCUMENT":
            row = await self._document_row(session, principal, entity_id)
            return {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "title": str(row["title"]),
                "status": str(row["status"]),
            }
        if entity_type == "APPROVAL":
            approvals = await self._table(session, "work_approvals")
            approval_links = await self._table(session, "work_approval_workspaces")
            approval = (
                (
                    await session.execute(
                        select(approvals).where(
                            approvals.c.approval_id == entity_id,
                            *self._visible(approvals, approval_links, "approval_id", principal),
                        )
                    )
                )
                .mappings()
                .first()
            )
            if approval is None:
                raise self._not_found()
            if approval["requested_action"] is not None:
                await self._material_subject(session, principal, dict(approval), mode="read")
                return {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "title": str(approval["requested_action"]),
                    "status": str(approval["status"]),
                }
            subject = await self._relation_projection(
                session, principal, str(approval["subject_type"]), str(approval["subject_id"])
            )
            return {
                "entity_type": entity_type,
                "entity_id": entity_id,
                "title": subject["title"],
                "status": str(approval["status"]),
            }
        configuration = resources.get(entity_type)
        if configuration is None:
            raise self._not_found()
        table_name, link_name, identifier, title = configuration
        table = await self._table(session, table_name)
        link = await self._table(session, link_name)
        record = (
            await session.execute(
                select(table.c[title], table.c.status).where(
                    table.c[identifier] == entity_id,
                    *self._visible(table, link, identifier, principal),
                )
            )
        ).first()
        if record is None:
            raise self._not_found()
        return {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "title": str(record[0]),
            "status": str(record[1]),
        }

    async def list_relations(
        self, principal: Principal, entity_type: str, entity_id: str
    ) -> list[dict[str, str]]:
        async with self._session_factory() as session:
            await self._assert_shared_entity(session, principal, entity_type, entity_id)
            if entity_type == "PROJECT":
                return await self.list_project_relations(principal, entity_id)
            links = await self._table(session, "shared_work_document_links")
            predicates = [
                links.c.tenant_id == principal.tenant_id,
                links.c.organization_id == principal.organization_id,
                links.c.workspace_id == principal.workspace_id,
            ]
            if entity_type == "DOCUMENT":
                predicates.append(links.c.document_id == entity_id)
            elif entity_type in {"TASK", "APPROVAL"}:
                predicates.extend(
                    (links.c.target_type == entity_type, links.c.target_id == entity_id)
                )
            else:
                predicates.append(links.c.link_id == "")
            linked = (await session.execute(select(links).where(*predicates))).mappings().all()
            result = []
            for item in linked:
                target_type, target_id = (
                    (str(item["target_type"]), str(item["target_id"]))
                    if entity_type == "DOCUMENT"
                    else ("DOCUMENT", str(item["document_id"]))
                )
                result.append(
                    await self._relation_projection(session, principal, target_type, target_id)
                )
            direct: list[tuple[str, str]] = []
            if entity_type == "DOCUMENT":
                document = await self._document_row(session, principal, entity_id)
                if document.get("project_id"):
                    direct.append(("PROJECT", str(document["project_id"])))
            elif entity_type == "TASK":
                tasks = await self._table(session, "tasks")
                project_id = (
                    await session.execute(
                        select(tasks.c.project_id).where(
                            tasks.c.task_id == entity_id,
                            tasks.c.tenant_id == principal.tenant_id,
                            tasks.c.organization_id == principal.organization_id,
                        )
                    )
                ).scalar_one_or_none()
                if project_id:
                    direct.append(("PROJECT", str(project_id)))
                findings = await self._table(session, "work_findings")
                finding_links = await self._table(session, "work_finding_workspaces")
                finding_ids: Sequence[object] = (
                    (
                        await session.execute(
                            select(findings.c.finding_id).where(
                                findings.c.corrective_action_task_id == entity_id,
                                *self._visible(findings, finding_links, "finding_id", principal),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                direct.extend(("FINDING", str(item)) for item in finding_ids)
            elif entity_type == "APPROVAL":
                approvals = await self._table(session, "work_approvals")
                subject = (
                    await session.execute(
                        select(
                            approvals.c.subject_type,
                            approvals.c.subject_id,
                        ).where(
                            approvals.c.approval_id == entity_id,
                            approvals.c.tenant_id == principal.tenant_id,
                            approvals.c.organization_id == principal.organization_id,
                        )
                    )
                ).first()
                if subject and str(subject[0]) in self._approval_subjects:
                    direct.append((str(subject[0]), str(subject[1])))
            elif entity_type in {"FINDING", "REPORT"}:
                table_name, identifier = (
                    ("work_findings", "finding_id")
                    if entity_type == "FINDING"
                    else ("work_reports", "report_id")
                )
                table = await self._table(session, table_name)
                record = (
                    (
                        await session.execute(
                            select(table).where(
                                table.c[identifier] == entity_id,
                                table.c.tenant_id == principal.tenant_id,
                                table.c.organization_id == principal.organization_id,
                            )
                        )
                    )
                    .mappings()
                    .first()
                )
                if record and record.get("project_id"):
                    direct.append(("PROJECT", str(record["project_id"])))
                if entity_type == "FINDING" and record and record.get("corrective_action_task_id"):
                    direct.append(("TASK", str(record["corrective_action_task_id"])))
            for target_type, target_id in direct:
                result.append(
                    await self._relation_projection(session, principal, target_type, target_id)
                )
            return result

    async def link_document(
        self, principal: Principal, document_id: str, target_type: str, target_id: str
    ) -> tuple[dict[str, str], bool]:
        async with self._session_factory() as session, session.begin():
            await self._document_row(session, principal, document_id)
            target = await self._relation_projection(session, principal, target_type, target_id)
            links = await self._table(session, "shared_work_document_links")
            predicates = (
                links.c.tenant_id == principal.tenant_id,
                links.c.organization_id == principal.organization_id,
                links.c.workspace_id == principal.workspace_id,
                links.c.document_id == document_id,
                links.c.target_type == target_type,
                links.c.target_id == target_id,
            )
            existing = (
                await session.execute(select(links.c.link_id).where(*predicates))
            ).scalar_one_or_none()
            if existing is not None:
                return target, False
            await session.execute(
                insert(links).values(
                    link_id=uuid4().hex,
                    tenant_id=principal.tenant_id,
                    organization_id=principal.organization_id,
                    workspace_id=principal.workspace_id,
                    document_id=document_id,
                    target_type=target_type,
                    target_id=target_id,
                    linked_by=principal.actor_id,
                    linked_at=datetime.now(UTC),
                )
            )
            return target, True

    async def list_checklist(
        self, principal: Principal, entity_type: str, entity_id: str
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await self._assert_shared_entity(session, principal, entity_type, entity_id)
            table = await self._table(session, "shared_work_checklist_items")
            rows = (
                (
                    await session.execute(
                        select(table)
                        .where(
                            table.c.tenant_id == principal.tenant_id,
                            table.c.organization_id == principal.organization_id,
                            table.c.workspace_id == principal.workspace_id,
                            table.c.entity_type == entity_type,
                            table.c.entity_id == entity_id,
                        )
                        .order_by(table.c.created_at, table.c.item_id)
                    )
                )
                .mappings()
                .all()
            )
            return [self._checklist_projection(row) for row in rows]

    @staticmethod
    def _checklist_projection(row: Any) -> dict[str, Any]:
        fields = (
            "item_id",
            "entity_type",
            "entity_id",
            "body",
            "completed",
            "created_by",
            "completed_by",
            "created_at",
            "completed_at",
        )
        return dict(jsonable_encoder({field: row[field] for field in fields}))

    async def create_checklist_item(
        self, principal: Principal, entity_type: str, entity_id: str, body: str
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            await self._assert_shared_entity(session, principal, entity_type, entity_id)
            table = await self._table(session, "shared_work_checklist_items")
            row = (
                (
                    await session.execute(
                        insert(table)
                        .values(
                            item_id=uuid4().hex,
                            tenant_id=principal.tenant_id,
                            organization_id=principal.organization_id,
                            workspace_id=principal.workspace_id,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            body=body.strip(),
                            completed=False,
                            created_by=principal.actor_id,
                            created_at=datetime.now(UTC),
                        )
                        .returning(table)
                    )
                )
                .mappings()
                .one()
            )
            return self._checklist_projection(row)

    async def complete_checklist_item(
        self, principal: Principal, entity_type: str, entity_id: str, item_id: str
    ) -> tuple[dict[str, Any], bool]:
        async with self._session_factory() as session, session.begin():
            await self._assert_shared_entity(session, principal, entity_type, entity_id)
            table = await self._table(session, "shared_work_checklist_items")
            row = (
                (
                    await session.execute(
                        select(table)
                        .where(
                            table.c.item_id == item_id,
                            table.c.tenant_id == principal.tenant_id,
                            table.c.organization_id == principal.organization_id,
                            table.c.workspace_id == principal.workspace_id,
                            table.c.entity_type == entity_type,
                            table.c.entity_id == entity_id,
                        )
                        .with_for_update()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise self._not_found()
            if row["completed"]:
                return self._checklist_projection(row), False
            changed = (
                (
                    await session.execute(
                        update(table)
                        .where(table.c.item_id == item_id)
                        .values(
                            completed=True,
                            completed_by=principal.actor_id,
                            completed_at=datetime.now(UTC),
                        )
                        .returning(table)
                    )
                )
                .mappings()
                .one()
            )
            return self._checklist_projection(changed), True

    async def list_evidence(
        self, principal: Principal, entity_type: str, entity_id: str
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await self._assert_shared_entity(session, principal, entity_type, entity_id)
            links = await self._table(session, "shared_work_evidence_links")
            evidence = await self._table(session, "evidence_refs", schema="evidence")
            sources = await self._table(session, "sources")
            rows = (
                (
                    await session.execute(
                        select(
                            links.c.link_id,
                            links.c.entity_type,
                            links.c.entity_id,
                            links.c.evidence_id,
                            evidence.c.source_id,
                            sources.c.title.label("source_title"),
                            evidence.c.source_version,
                            evidence.c.content_hash,
                            evidence.c.validation_status,
                            links.c.linked_by,
                            links.c.linked_at,
                        )
                        .join(evidence, evidence.c.evidence_id == links.c.evidence_id)
                        .outerjoin(
                            sources,
                            and_(
                                sources.c.source_id == evidence.c.source_id,
                                sources.c.tenant_id == principal.tenant_id,
                                sources.c.organization_id == principal.organization_id,
                                sources.c.workspace_id == principal.workspace_id,
                            ),
                        )
                        .where(
                            links.c.entity_type == entity_type,
                            links.c.entity_id == entity_id,
                            links.c.tenant_id == principal.tenant_id,
                            links.c.organization_id == principal.organization_id,
                            links.c.workspace_id == principal.workspace_id,
                            evidence.c.tenant_id == principal.tenant_id,
                            evidence.c.organization_id == principal.organization_id,
                            evidence.c.workspace_id == principal.workspace_id,
                        )
                        .order_by(links.c.linked_at.desc())
                    )
                )
                .mappings()
                .all()
            )
            return [jsonable_encoder(dict(row)) for row in rows]

    async def list_evidence_candidates(self, principal: Principal) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await self._verify_workspace(session, principal)
            evidence = await self._table(session, "evidence_refs", schema="evidence")
            sources = await self._table(session, "sources")
            rows = (
                (
                    await session.execute(
                        select(
                            evidence.c.evidence_id,
                            evidence.c.source_id,
                            sources.c.title.label("source_title"),
                            evidence.c.source_version,
                            evidence.c.content_hash,
                            evidence.c.validation_status,
                            evidence.c.captured_at,
                        )
                        .outerjoin(
                            sources,
                            and_(
                                sources.c.source_id == evidence.c.source_id,
                                sources.c.tenant_id == principal.tenant_id,
                                sources.c.organization_id == principal.organization_id,
                                sources.c.workspace_id == principal.workspace_id,
                            ),
                        )
                        .where(
                            evidence.c.tenant_id == principal.tenant_id,
                            evidence.c.organization_id == principal.organization_id,
                            evidence.c.workspace_id == principal.workspace_id,
                            evidence.c.validation_status == "VERIFIED",
                        )
                        .order_by(evidence.c.captured_at.desc())
                        .limit(200)
                    )
                )
                .mappings()
                .all()
            )
            return [jsonable_encoder(dict(row)) for row in rows]

    async def link_evidence(
        self, principal: Principal, entity_type: str, entity_id: str, evidence_id: str
    ) -> tuple[dict[str, Any], bool]:
        async with self._session_factory() as session, session.begin():
            await self._assert_shared_entity(session, principal, entity_type, entity_id)
            evidence = await self._table(session, "evidence_refs", schema="evidence")
            sources = await self._table(session, "sources")
            source = (
                (
                    await session.execute(
                        select(evidence).where(
                            evidence.c.evidence_id == evidence_id,
                            evidence.c.tenant_id == principal.tenant_id,
                            evidence.c.organization_id == principal.organization_id,
                            evidence.c.workspace_id == principal.workspace_id,
                            evidence.c.validation_status == "VERIFIED",
                        )
                    )
                )
                .mappings()
                .first()
            )
            if source is None:
                raise self._not_found()
            source_title = (
                await session.execute(
                    select(sources.c.title).where(
                        sources.c.source_id == source["source_id"],
                        sources.c.tenant_id == principal.tenant_id,
                        sources.c.organization_id == principal.organization_id,
                        sources.c.workspace_id == principal.workspace_id,
                    )
                )
            ).scalar_one_or_none()
            links = await self._table(session, "shared_work_evidence_links")
            existing = (
                (
                    await session.execute(
                        select(links).where(
                            links.c.tenant_id == principal.tenant_id,
                            links.c.organization_id == principal.organization_id,
                            links.c.workspace_id == principal.workspace_id,
                            links.c.entity_type == entity_type,
                            links.c.entity_id == entity_id,
                            links.c.evidence_id == evidence_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if existing is None:
                linked = (
                    (
                        await session.execute(
                            insert(links)
                            .values(
                                link_id=uuid4().hex,
                                tenant_id=principal.tenant_id,
                                organization_id=principal.organization_id,
                                workspace_id=principal.workspace_id,
                                entity_type=entity_type,
                                entity_id=entity_id,
                                evidence_id=evidence_id,
                                linked_by=principal.actor_id,
                                linked_at=datetime.now(UTC),
                            )
                            .returning(links)
                        )
                    )
                    .mappings()
                    .one()
                )
            else:
                linked = existing
            result = {
                "link_id": linked["link_id"],
                "entity_type": entity_type,
                "entity_id": entity_id,
                "evidence_id": evidence_id,
                "source_id": source["source_id"],
                "source_title": source_title,
                "source_version": source["source_version"],
                "content_hash": source["content_hash"],
                "validation_status": source["validation_status"],
                "linked_by": linked["linked_by"],
                "linked_at": linked["linked_at"],
            }
            return jsonable_encoder(result), existing is None

    async def list_activity(
        self, principal: Principal, entity_type: str, entity_id: str
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await self._assert_shared_entity(session, principal, entity_type, entity_id)
            audit = await self._table(session, "audit_records", schema="audit")
            actors = await self._table(session, "actors")
            rows = (
                (
                    await session.execute(
                        select(
                            audit.c.audit_id,
                            audit.c.event_type,
                            audit.c.actor_id,
                            actors.c.display_name.label("actor_name"),
                            audit.c.occurred_at,
                        )
                        .outerjoin(actors, actors.c.actor_id == audit.c.actor_id)
                        .where(
                            audit.c.entity_type == entity_type.lower(),
                            audit.c.entity_id == entity_id,
                            audit.c.tenant_id == principal.tenant_id,
                            audit.c.organization_id == principal.organization_id,
                            audit.c.workspace_id == principal.workspace_id,
                            audit.c.outcome == "SUCCEEDED",
                        )
                        .order_by(audit.c.occurred_at.desc(), audit.c.audit_id.desc())
                        .limit(200)
                    )
                )
                .mappings()
                .all()
            )
            return [
                jsonable_encoder(
                    {**dict(row), "actor_name": row["actor_name"] or "Aktor tidak tersedia"}
                )
                for row in rows
            ]

    async def list_comments(
        self, principal: Principal, entity_type: str, entity_id: str
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await self._assert_shared_entity(session, principal, entity_type, entity_id)
            comments = await self._table(session, "shared_work_comments")
            actors = await self._table(session, "actors")
            rows = (
                (
                    await session.execute(
                        select(
                            comments.c.comment_id,
                            comments.c.entity_type,
                            comments.c.entity_id,
                            comments.c.actor_id,
                            actors.c.display_name.label("actor_name"),
                            comments.c.body,
                            comments.c.created_at,
                        )
                        .outerjoin(actors, actors.c.actor_id == comments.c.actor_id)
                        .where(
                            comments.c.tenant_id == principal.tenant_id,
                            comments.c.organization_id == principal.organization_id,
                            comments.c.workspace_id == principal.workspace_id,
                            comments.c.entity_type == entity_type,
                            comments.c.entity_id == entity_id,
                        )
                        .order_by(comments.c.created_at.desc())
                    )
                )
                .mappings()
                .all()
            )
            return [
                jsonable_encoder(
                    {**dict(row), "actor_name": row["actor_name"] or "Aktor tidak tersedia"}
                )
                for row in rows
            ]

    async def create_comment(
        self, principal: Principal, entity_type: str, entity_id: str, body: str
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            await self._assert_shared_entity(session, principal, entity_type, entity_id)
            comments = await self._table(session, "shared_work_comments")
            actors = await self._table(session, "actors")
            name = (
                await session.execute(
                    select(actors.c.display_name).where(
                        actors.c.actor_id == principal.actor_id,
                        actors.c.tenant_id == principal.tenant_id,
                        actors.c.organization_id == principal.organization_id,
                    )
                )
            ).scalar_one_or_none()
            row = (
                (
                    await session.execute(
                        insert(comments)
                        .values(
                            comment_id=uuid4().hex,
                            tenant_id=principal.tenant_id,
                            organization_id=principal.organization_id,
                            workspace_id=principal.workspace_id,
                            entity_type=entity_type,
                            entity_id=entity_id,
                            actor_id=principal.actor_id,
                            body=body.strip(),
                            created_at=datetime.now(UTC),
                        )
                        .returning(comments)
                    )
                )
                .mappings()
                .one()
            )
            return dict(
                jsonable_encoder(
                    {
                        "comment_id": row["comment_id"],
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "actor_id": principal.actor_id,
                        "actor_name": name or "Aktor tidak tersedia",
                        "body": row["body"],
                        "created_at": row["created_at"],
                    }
                )
            )

    async def _verify_workspace(self, session: AsyncSession, principal: Principal) -> None:
        workspaces = await self._table(session, "workspaces")
        available = await session.execute(
            select(workspaces.c.workspace_id).where(
                workspaces.c.workspace_id == principal.workspace_id,
                workspaces.c.tenant_id == principal.tenant_id,
                workspaces.c.organization_id == principal.organization_id,
                workspaces.c.active.is_(True),
            )
        )
        if available.scalar_one_or_none() is None:
            raise PlatformError(
                "WORKSPACE_ACCESS_DENIED", "Active workspace is unavailable.", status_code=403
            )

    async def executive_summary(self, principal: Principal) -> ExecutiveSharedWorkSummary:
        """Read existing workspace authority, with exact counts and bounded entity previews.

        Executive company context never overrides entity workspace links. All queries share
        a read-only repeatable snapshot; no entity or lifecycle is copied or mutated.
        """
        if not principal.active or "EXECUTIVE" not in principal.roles:
            raise PlatformError(
                "EXECUTIVE_ROLE_DENIED", "Executive access is required.", status_code=403
            )
        if "work.read" not in principal.permissions:
            raise PlatformError(
                "WORK_PERMISSION_DENIED", "Shared Work read is required.", status_code=403
            )
        counts: dict[str, int] = {}
        result: dict[str, Any] = {"counts": counts}
        times: list[datetime] = []
        now = datetime.now(UTC)
        specifications = (
            ("projects", "projects", "project_workspaces", "project_id"),
            ("tasks", "tasks", "task_workspaces", "task_id"),
            ("approvals", "work_approvals", "work_approval_workspaces", "approval_id"),
            ("findings", "work_findings", "work_finding_workspaces", "finding_id"),
            ("reports", "work_reports", "work_report_workspaces", "report_id"),
            ("documents", "documents", None, "document_id"),
        )
        async with self._session_factory() as session, session.begin():
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            await self._verify_workspace(session, principal)
            for name, table_name, link_name, identifier in specifications:
                table = await self._table(session, table_name)
                if link_name is None:
                    predicates = self._document_scope(table, principal)
                else:
                    link = await self._table(session, link_name)
                    predicates = self._visible(table, link, identifier, principal)
                metrics: dict[str, Any] = {}
                priority: list[Any] = []
                if name == "projects":
                    metrics = {
                        "active_projects": table.c.status == "ACTIVE",
                        "on_hold_projects": table.c.status == "ON_HOLD",
                        "completed_projects": table.c.status == "COMPLETED",
                    }
                elif name == "tasks":
                    active = table.c.status.not_in(("COMPLETED", "CANCELLED"))
                    metrics = {
                        "overdue_tasks": and_(active, table.c.due_at < now),
                        "blocked_tasks": table.c.status == "BLOCKED",
                        "critical_tasks": and_(active, table.c.priority == "CRITICAL"),
                        "pending_review_tasks": table.c.status == "UNDER_REVIEW",
                    }
                    priority = [metrics["overdue_tasks"].desc().nulls_last()]
                elif name == "approvals":
                    predicates = (
                        *predicates,
                        table.c.subject_type.in_(self._approval_subjects),
                    )
                    metrics = {"pending_approvals": table.c.status == "PENDING"}
                    priority = [metrics["pending_approvals"].desc()]
                elif name == "findings":
                    active = table.c.status.in_(
                        ("OPEN", "ASSIGNED", "IN_PROGRESS", "PENDING_VERIFICATION")
                    )
                    metrics = {
                        "open_findings": table.c.status == "OPEN",
                        "active_findings": active,
                        "critical_findings": and_(active, table.c.severity == "CRITICAL"),
                        "high_findings": and_(active, table.c.severity == "HIGH"),
                        "pending_verification_findings": table.c.status == "PENDING_VERIFICATION",
                    }
                    priority = [active.desc(), (table.c.severity == "CRITICAL").desc()]
                updated = (
                    func.coalesce(table.c.decided_at, table.c.requested_at)
                    if name == "approvals"
                    else table.c.updated_at
                )
                aggregate = (
                    (
                        await session.execute(
                            select(
                                func.count().label(name),
                                *(
                                    func.count().filter(condition).label(key)
                                    for key, condition in metrics.items()
                                ),
                                func.max(updated).label("last_updated_at"),
                            )
                            .select_from(table)
                            .where(*predicates)
                        )
                    )
                    .mappings()
                    .one()
                )
                counts.update({key: int(aggregate[key]) for key in (name, *metrics)})
                if aggregate["last_updated_at"] is not None:
                    times.append(aggregate["last_updated_at"])
                rows = (
                    (
                        await session.execute(
                            select(table)
                            .where(*predicates)
                            .order_by(*priority, updated.desc(), table.c[identifier])
                            .limit(50)
                        )
                    )
                    .mappings()
                    .all()
                )
                result[name] = [
                    self._document_projection(dict(row))
                    if name == "documents"
                    else self._projection(dict(row), principal)
                    for row in rows
                ]
        result["last_updated_at"] = max(times).isoformat() if times else None
        return cast("ExecutiveSharedWorkSummary", result)

    async def list_projects(
        self, principal: Principal, *, status: str | None, search: str | None
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            projects = await self._table(session, "projects")
            links = await self._table(session, "project_workspaces")
            predicates = list(self._visible(projects, links, "project_id", principal))
            if status:
                predicates.append(projects.c.status == status)
            if search:
                pattern = f"%{search}%"
                predicates.append(
                    or_(projects.c.code.ilike(pattern), projects.c.name.ilike(pattern))
                )
            rows = (
                (
                    await session.execute(
                        select(projects)
                        .where(*predicates)
                        .order_by(projects.c.created_at.desc(), projects.c.project_id)
                        .limit(200)
                    )
                )
                .mappings()
                .all()
            )
            return [self._projection(dict(row), principal) for row in rows]

    async def list_company_projects_progress(self, principal: Principal) -> list[dict[str, Any]]:
        """Company-scoped project progress for authorized executive callers."""
        if not principal.active or "EXECUTIVE" not in principal.roles:
            raise PlatformError(
                "EXECUTIVE_ROLE_DENIED", "Executive access is required.", status_code=403
            )
        if principal.data_scope != DataScope.COMPANY:
            raise PlatformError(
                "EXECUTIVE_SCOPE_DENIED", "Company data scope is required.", status_code=403
            )
        if not ({"project.read", "work.read", "strategy.read"} & principal.permissions):
            raise PlatformError(
                "WORK_PERMISSION_DENIED",
                "Executive company read permission is required.",
                status_code=403,
            )
        async with self._session_factory() as session:
            await self._verify_workspace(session, principal)
            projects = await self._table(session, "projects")
            tasks = await self._table(session, "tasks")
            project_rows = (
                (
                    await session.execute(
                        select(projects.c.project_id, projects.c.name)
                        .where(
                            projects.c.tenant_id == principal.tenant_id,
                            projects.c.organization_id == principal.organization_id,
                        )
                        .order_by(projects.c.created_at.desc(), projects.c.project_id)
                        .limit(20)
                    )
                )
                .mappings()
                .all()
            )
            if not project_rows:
                return []
            project_ids = [str(row["project_id"]) for row in project_rows]
            task_rows = (
                (
                    await session.execute(
                        select(tasks.c.project_id, tasks.c.status).where(
                            tasks.c.tenant_id == principal.tenant_id,
                            tasks.c.organization_id == principal.organization_id,
                            tasks.c.project_id.in_(project_ids),
                        )
                    )
                )
                .mappings()
                .all()
            )
            tasks_by_project: dict[str, list[dict[str, Any]]] = {pid: [] for pid in project_ids}
            for task in task_rows:
                pid = str(task["project_id"])
                if pid in tasks_by_project:
                    tasks_by_project[pid].append(dict(task))

            return [
                {
                    "project_id": str(p["project_id"]),
                    "name": str(p["name"] or "Proyek tanpa nama"),
                    "progress_percentage": calculate_project_progress(
                        tasks_by_project[str(p["project_id"])]
                    ),
                }
                for p in project_rows
            ]

    async def list_project_relations(
        self, principal: Principal, project_id: str
    ) -> list[dict[str, str]]:
        async with self._session_factory() as session:
            await self._visible_project_id(session, principal, project_id)
            result: list[dict[str, str]] = []
            tasks = await self._table(session, "tasks")
            task_links = await self._table(session, "task_workspaces")
            task_rows = (
                (
                    await session.execute(
                        select(tasks.c.task_id, tasks.c.title, tasks.c.status).where(
                            tasks.c.project_id == project_id,
                            *self._visible(tasks, task_links, "task_id", principal),
                        )
                    )
                )
                .mappings()
                .all()
            )
            task_ids = [str(row["task_id"]) for row in task_rows]
            task_titles = {str(row["task_id"]): str(row["title"]) for row in task_rows}
            projects = await self._table(session, "projects")
            project_title: object = (
                await session.execute(
                    select(projects.c.name).where(projects.c.project_id == project_id)
                )
            ).scalar_one()
            for row in task_rows:
                result.append(
                    {
                        "entity_type": "TASK",
                        "entity_id": str(row["task_id"]),
                        "title": str(row["title"]),
                        "status": str(row["status"]),
                    }
                )
            documents = await self._table(session, "documents")
            for row in (
                (
                    await session.execute(
                        select(
                            documents.c.document_id, documents.c.title, documents.c.status
                        ).where(
                            documents.c.project_id == project_id,
                            *self._document_scope(documents, principal),
                        )
                    )
                )
                .mappings()
                .all()
            ):
                result.append(
                    {
                        "entity_type": "DOCUMENT",
                        "entity_id": str(row["document_id"]),
                        "title": str(row["title"]),
                        "status": str(row["status"]),
                    }
                )
            findings = await self._table(session, "work_findings")
            finding_links = await self._table(session, "work_finding_workspaces")
            for row in (
                (
                    await session.execute(
                        select(findings.c.finding_id, findings.c.title, findings.c.status).where(
                            findings.c.project_id == project_id,
                            *self._visible(findings, finding_links, "finding_id", principal),
                        )
                    )
                )
                .mappings()
                .all()
            ):
                result.append(
                    {
                        "entity_type": "FINDING",
                        "entity_id": str(row["finding_id"]),
                        "title": str(row["title"]),
                        "status": str(row["status"]),
                    }
                )
            reports = await self._table(session, "work_reports")
            report_links = await self._table(session, "work_report_workspaces")
            for row in (
                (
                    await session.execute(
                        select(reports.c.report_id, reports.c.title, reports.c.status).where(
                            reports.c.project_id == project_id,
                            *self._visible(reports, report_links, "report_id", principal),
                        )
                    )
                )
                .mappings()
                .all()
            ):
                result.append(
                    {
                        "entity_type": "REPORT",
                        "entity_id": str(row["report_id"]),
                        "title": str(row["title"]),
                        "status": str(row["status"]),
                    }
                )
            approvals = await self._table(session, "work_approvals")
            approval_links = await self._table(session, "work_approval_workspaces")
            for row in (
                (
                    await session.execute(
                        select(
                            approvals.c.approval_id,
                            approvals.c.subject_type,
                            approvals.c.subject_id,
                            approvals.c.status,
                        ).where(
                            or_(
                                and_(
                                    approvals.c.subject_type == "PROJECT",
                                    approvals.c.subject_id == project_id,
                                ),
                                and_(
                                    approvals.c.subject_type == "TASK",
                                    approvals.c.subject_id.in_(task_ids),
                                ),
                            ),
                            *self._visible(approvals, approval_links, "approval_id", principal),
                        )
                    )
                )
                .mappings()
                .all()
            ):
                result.append(
                    {
                        "entity_type": "APPROVAL",
                        "entity_id": str(row["approval_id"]),
                        "title": (
                            str(project_title)
                            if row["subject_type"] == "PROJECT"
                            else task_titles[str(row["subject_id"])]
                        ),
                        "status": str(row["status"]),
                    }
                )
            return result

    async def get_project(self, principal: Principal, project_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            projects = await self._table(session, "projects")
            links = await self._table(session, "project_workspaces")
            row = (
                (
                    await session.execute(
                        select(projects).where(
                            projects.c.project_id == project_id,
                            *self._visible(projects, links, "project_id", principal),
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise self._not_found()
            return self._projection(dict(row), principal)

    async def create_project(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            await self._verify_workspace(session, principal)
            owner_actor_id = payload.get("owner_actor_id") or principal.actor_id
            await self._validate_project_owner(session, principal, owner_actor_id)
            projects = await self._table(session, "projects")
            links = await self._table(session, "project_workspaces")
            project_id = uuid4().hex
            now = datetime.now(UTC)
            values = {
                **payload,
                "project_id": project_id,
                "tenant_id": principal.tenant_id,
                "organization_id": principal.organization_id,
                "status": "PLANNED",
                "owner_actor_id": owner_actor_id,
                "owning_workspace_id": principal.workspace_id,
                "priority": payload.get("priority", "NORMAL"),
                "created_at": now,
                "updated_at": now,
            }
            for key in ("start_date", "target_end_date"):
                if values.get(key) is not None:
                    values[key] = date.fromisoformat(values[key])
            row = (
                (await session.execute(insert(projects).values(**values).returning(projects)))
                .mappings()
                .one()
            )
            await session.execute(
                insert(links).values(project_id=project_id, workspace_id=principal.workspace_id)
            )
            return self._projection(dict(row), principal)

    async def update_project(
        self, principal: Principal, project_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            projects = await self._table(session, "projects")
            links = await self._table(session, "project_workspaces")
            current = await self._locked_record(
                session, projects, links, "project_id", project_id, principal
            )
            if current["status"] == "ARCHIVED":
                raise PlatformError(
                    "PROJECT_ARCHIVED", "Archived projects cannot be changed.", status_code=409
                )
            if payload.get("owner_actor_id") is not None:
                await self._validate_project_owner(session, principal, payload["owner_actor_id"])
            values = {**payload, "updated_at": datetime.now(UTC)}
            for key in ("start_date", "target_end_date"):
                if key in values and values[key] is not None:
                    values[key] = date.fromisoformat(values[key])
            row = (
                (
                    await session.execute(
                        update(projects)
                        .where(projects.c.project_id == project_id)
                        .values(**values)
                        .returning(projects)
                    )
                )
                .mappings()
                .one()
            )
            return self._projection(dict(row), principal)

    async def _validate_project_owner(
        self, session: AsyncSession, principal: Principal, actor_id: str
    ) -> None:
        actors = await self._table(session, "actors")
        members = await self._table(session, "workspace_memberships")
        now = datetime.now(UTC)
        permission_refs = await session.scalar(
            select(members.c.permission_refs)
            .join(
                actors,
                actors.c.actor_id == members.c.actor_id,
            )
            .where(
                actors.c.actor_id == actor_id,
                actors.c.tenant_id == principal.tenant_id,
                actors.c.organization_id == principal.organization_id,
                actors.c.active.is_(True),
                members.c.tenant_id == principal.tenant_id,
                members.c.organization_id == principal.organization_id,
                members.c.workspace_id == principal.workspace_id,
                members.c.active.is_(True),
                members.c.revoked_at.is_(None),
                members.c.effective_at <= now,
                or_(members.c.expires_at.is_(None), members.c.expires_at > now),
            )
        )
        owner_permissions = {"project.read", "work.read"}
        if actor_id == principal.actor_id:
            owner_permissions.update({"project.create", "work.write"})
        if not isinstance(permission_refs, list) or not owner_permissions.intersection(
            permission_refs
        ):
            raise self._not_found()

    async def archive_project(
        self, principal: Principal, project_id: str
    ) -> tuple[dict[str, Any], bool]:
        async with self._session_factory() as session, session.begin():
            projects = await self._table(session, "projects")
            links = await self._table(session, "project_workspaces")
            current = await self._locked_record(
                session, projects, links, "project_id", project_id, principal
            )
            if current["status"] == "ARCHIVED":
                return self._projection(current, principal), False
            row = (
                (
                    await session.execute(
                        update(projects)
                        .where(projects.c.project_id == project_id)
                        .values(status="ARCHIVED", updated_at=datetime.now(UTC))
                        .returning(projects)
                    )
                )
                .mappings()
                .one()
            )
            return self._projection(dict(row), principal), True

    async def list_tasks(
        self,
        principal: Principal,
        *,
        status: str | None,
        priority: str | None,
        search: str | None,
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            tasks = await self._table(session, "tasks")
            links = await self._table(session, "task_workspaces")
            predicates = list(self._visible(tasks, links, "task_id", principal))
            if status:
                predicates.append(tasks.c.status == status)
            if priority:
                predicates.append(tasks.c.priority == priority)
            if search:
                pattern = f"%{search}%"
                predicates.append(
                    or_(tasks.c.title.ilike(pattern), tasks.c.description.ilike(pattern))
                )
            rows = (
                (
                    await session.execute(
                        select(tasks)
                        .where(*predicates)
                        .order_by(tasks.c.created_at.desc(), tasks.c.task_id)
                        .limit(200)
                    )
                )
                .mappings()
                .all()
            )
            return [self._projection(dict(row), principal) for row in rows]

    async def get_task(self, principal: Principal, task_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            tasks = await self._table(session, "tasks")
            links = await self._table(session, "task_workspaces")
            row = (
                (
                    await session.execute(
                        select(tasks).where(
                            tasks.c.task_id == task_id,
                            *self._visible(tasks, links, "task_id", principal),
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise self._not_found()
            return self._projection(dict(row), principal)

    async def create_task(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            return await self.create_task_in_session(session, principal, payload)

    async def create_task_in_session(
        self, session: AsyncSession, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Reuse the canonical task command inside a governed orchestration transaction."""
        await self._verify_workspace(session, principal)
        project_id = payload.get("project_id")
        if project_id is not None:
            await self._visible_project_id(session, principal, project_id)
        tasks = await self._table(session, "tasks")
        links = await self._table(session, "task_workspaces")
        task_id = uuid4().hex
        now = datetime.now(UTC)
        values = {
            **payload,
            "task_id": task_id,
            "tenant_id": principal.tenant_id,
            "organization_id": principal.organization_id,
            "status": "OPEN",
            "priority": payload.get("priority", "NORMAL"),
            "owner_actor_id": None,
            "created_by": principal.actor_id,
            "created_at": now,
            "updated_at": now,
        }
        if values.get("due_at") is not None:
            values["due_at"] = datetime.fromisoformat(values["due_at"].replace("Z", "+00:00"))
        if values.get("start_date") is not None:
            values["start_date"] = date.fromisoformat(values["start_date"])
        row = (
            (await session.execute(insert(tasks).values(**values).returning(tasks)))
            .mappings()
            .one()
        )
        await session.execute(
            insert(links).values(task_id=task_id, workspace_id=principal.workspace_id)
        )
        return self._projection(dict(row), principal)

    async def update_task(
        self, principal: Principal, task_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            tasks = await self._table(session, "tasks")
            links = await self._table(session, "task_workspaces")
            current = await self._locked_record(
                session, tasks, links, "task_id", task_id, principal
            )
            if current["status"] in {"COMPLETED", "CANCELLED"}:
                raise PlatformError(
                    "TASK_CLOSED",
                    "Completed or cancelled tasks cannot be changed.",
                    status_code=409,
                )
            if payload.get("project_id") is not None:
                await self._visible_project_id(session, principal, payload["project_id"])
            values = {**payload, "updated_at": datetime.now(UTC)}
            if values.get("due_at") is not None:
                values["due_at"] = datetime.fromisoformat(values["due_at"].replace("Z", "+00:00"))
            if values.get("start_date") is not None:
                values["start_date"] = date.fromisoformat(values["start_date"])
            row = (
                (
                    await session.execute(
                        update(tasks)
                        .where(tasks.c.task_id == task_id)
                        .values(**values)
                        .returning(tasks)
                    )
                )
                .mappings()
                .one()
            )
            return self._projection(dict(row), principal)

    async def assign_task(
        self, principal: Principal, task_id: str, owner_actor_id: str
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            tasks = await self._table(session, "tasks")
            links = await self._table(session, "task_workspaces")
            current = await self._locked_record(
                session, tasks, links, "task_id", task_id, principal
            )
            if current["status"] in {"COMPLETED", "CANCELLED"}:
                raise PlatformError(
                    "TASK_CLOSED",
                    "Completed or cancelled tasks cannot be assigned.",
                    status_code=409,
                )
            actors = await self._table(session, "actors")
            memberships = await self._table(session, "workspace_memberships")
            workspaces = await self._table(session, "workspaces")
            now = datetime.now(UTC)
            eligible = await session.execute(
                select(memberships.c.permission_refs)
                .join(memberships, memberships.c.actor_id == actors.c.actor_id)
                .join(workspaces, workspaces.c.workspace_id == memberships.c.workspace_id)
                .where(
                    actors.c.actor_id == owner_actor_id,
                    actors.c.tenant_id == principal.tenant_id,
                    actors.c.organization_id == principal.organization_id,
                    actors.c.active.is_(True),
                    memberships.c.workspace_id == principal.workspace_id,
                    memberships.c.tenant_id == principal.tenant_id,
                    memberships.c.organization_id == principal.organization_id,
                    memberships.c.active.is_(True),
                    memberships.c.revoked_at.is_(None),
                    memberships.c.effective_at <= now,
                    or_(memberships.c.expires_at.is_(None), memberships.c.expires_at > now),
                    workspaces.c.tenant_id == principal.tenant_id,
                    workspaces.c.organization_id == principal.organization_id,
                    workspaces.c.active.is_(True),
                )
            )
            permission_refs = eligible.scalar_one_or_none()
            if not isinstance(permission_refs, list) or not {"task.read", "work.read"}.intersection(
                permission_refs
            ):
                raise self._not_found()
            row = (
                (
                    await session.execute(
                        update(tasks)
                        .where(tasks.c.task_id == task_id)
                        .values(owner_actor_id=owner_actor_id, updated_at=now)
                        .returning(tasks)
                    )
                )
                .mappings()
                .one()
            )
            return self._projection(dict(row), principal)

    async def complete_task(
        self, principal: Principal, task_id: str
    ) -> tuple[dict[str, Any], bool]:
        async with self._session_factory() as session, session.begin():
            tasks = await self._table(session, "tasks")
            links = await self._table(session, "task_workspaces")
            current = await self._locked_record(
                session, tasks, links, "task_id", task_id, principal
            )
            if current["status"] == "COMPLETED":
                return self._projection(current, principal), False
            if current["status"] == "CANCELLED":
                raise PlatformError(
                    "TASK_CANCELLED", "Cancelled tasks cannot be completed.", status_code=409
                )
            dependencies = await self._table(session, "shared_work_task_dependencies")
            blockers = tasks.alias("blockers")
            unresolved = (
                await session.execute(
                    select(dependencies.c.blocked_by_task_id)
                    .join(blockers, blockers.c.task_id == dependencies.c.blocked_by_task_id)
                    .where(
                        dependencies.c.tenant_id == principal.tenant_id,
                        dependencies.c.organization_id == principal.organization_id,
                        dependencies.c.workspace_id == principal.workspace_id,
                        dependencies.c.task_id == task_id,
                        blockers.c.status != "COMPLETED",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if unresolved is not None:
                raise PlatformError(
                    "TASK_DEPENDENCY_OPEN",
                    "Blocking tasks must be completed first.",
                    status_code=409,
                )
            row = (
                (
                    await session.execute(
                        update(tasks)
                        .where(tasks.c.task_id == task_id)
                        .values(status="COMPLETED", updated_at=datetime.now(UTC))
                        .returning(tasks)
                    )
                )
                .mappings()
                .one()
            )
            return self._projection(dict(row), principal), True

    async def change_task_dependency(
        self, principal: Principal, task_id: str, blocked_by_task_id: str, *, remove: bool
    ) -> tuple[dict[str, Any], bool]:
        if task_id == blocked_by_task_id:
            raise PlatformError(
                "TASK_DEPENDENCY_SELF", "A task cannot block itself.", status_code=409
            )
        async with self._session_factory() as session, session.begin():
            tasks = await self._table(session, "tasks")
            task_links = await self._table(session, "task_workspaces")
            locked = (
                (
                    await session.execute(
                        select(tasks)
                        .where(
                            tasks.c.task_id.in_((task_id, blocked_by_task_id)),
                            *self._visible(tasks, task_links, "task_id", principal),
                        )
                        .order_by(tasks.c.task_id)
                        .with_for_update()
                    )
                )
                .mappings()
                .all()
            )
            visible = {str(row["task_id"]): dict(row) for row in locked}
            if len(visible) != 2:
                raise self._not_found()
            current = visible[task_id]
            if current["status"] in {"COMPLETED", "CANCELLED"}:
                raise PlatformError(
                    "TASK_CLOSED",
                    "Completed or cancelled tasks cannot be changed.",
                    status_code=409,
                )
            table = await self._table(session, "shared_work_task_dependencies")
            predicates = (
                table.c.tenant_id == principal.tenant_id,
                table.c.organization_id == principal.organization_id,
                table.c.workspace_id == principal.workspace_id,
                table.c.task_id == task_id,
                table.c.blocked_by_task_id == blocked_by_task_id,
            )
            existing = (
                await session.execute(select(table.c.dependency_id).where(*predicates))
            ).scalar_one_or_none()
            if remove:
                if existing is None:
                    return self._projection(current, principal), False
                await session.execute(delete(table).where(*predicates))
                return self._projection(current, principal), True
            if existing is not None:
                return self._projection(current, principal), False
            edges = (
                await session.execute(
                    select(
                        table.c.task_id,
                        table.c.blocked_by_task_id,
                    ).where(
                        table.c.tenant_id == principal.tenant_id,
                        table.c.organization_id == principal.organization_id,
                        table.c.workspace_id == principal.workspace_id,
                    )
                )
            ).all()
            graph: dict[str, set[str]] = {}
            for dependent, blocker in edges:
                graph.setdefault(str(dependent), set()).add(str(blocker))
            pending = [blocked_by_task_id]
            visited: set[str] = set()
            while pending:
                candidate = pending.pop()
                if candidate == task_id:
                    raise PlatformError(
                        "TASK_DEPENDENCY_CYCLE",
                        "Task dependency would form a cycle.",
                        status_code=409,
                    )
                if candidate not in visited:
                    visited.add(candidate)
                    pending.extend(graph.get(candidate, set()))
            await session.execute(
                insert(table).values(
                    dependency_id=uuid4().hex,
                    tenant_id=principal.tenant_id,
                    organization_id=principal.organization_id,
                    workspace_id=principal.workspace_id,
                    task_id=task_id,
                    blocked_by_task_id=blocked_by_task_id,
                    linked_by=principal.actor_id,
                    linked_at=datetime.now(UTC),
                )
            )
            return self._projection(current, principal), True

    async def _visible_approval_subject(
        self, session: AsyncSession, principal: Principal, subject_type: str, subject_id: str
    ) -> None:
        if subject_type not in self._approval_subjects:
            await self._material_subject(
                session,
                principal,
                {"subject_type": subject_type, "subject_id": subject_id},
                mode="read",
            )
            return
        subject = self._approval_subjects.get(subject_type)
        if subject is None:
            raise PlatformError(
                "APPROVAL_SUBJECT_UNSUPPORTED", "Approval subject is unsupported.", status_code=422
            )
        table_name, link_name, identifier = subject
        table = await self._table(session, table_name)
        link = await self._table(session, link_name)
        found = await session.execute(
            select(table.c[identifier]).where(
                table.c[identifier] == subject_id,
                *self._visible(table, link, identifier, principal),
            )
        )
        if found.scalar_one_or_none() is None:
            raise self._not_found()

    async def list_approvals(
        self,
        principal: Principal,
        *,
        status: str | None,
        subject_type: str | None,
        search: str | None,
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            approvals = await self._table(session, "work_approvals")
            links = await self._table(session, "work_approval_workspaces")
            predicates = list(self._visible(approvals, links, "approval_id", principal))
            predicates.append(approvals.c.subject_type.in_(self.approval_subject_types))
            if status:
                predicates.append(approvals.c.status == status)
            if subject_type:
                predicates.append(approvals.c.subject_type == subject_type)
            if search:
                pattern = f"%{search}%"
                predicates.append(
                    or_(approvals.c.subject_id.ilike(pattern), approvals.c.reason.ilike(pattern))
                )
            rows = (
                (
                    await session.execute(
                        select(approvals)
                        .where(*predicates)
                        .order_by(approvals.c.requested_at.desc(), approvals.c.approval_id)
                        .limit(200)
                    )
                )
                .mappings()
                .all()
            )
            visible = []
            for row in rows:
                if row["requested_action"] is not None:
                    try:
                        await self._material_subject(session, principal, dict(row), mode="read")
                    except PlatformError as exc:
                        if exc.status_code in {403, 404}:
                            continue
                        raise
                visible.append(self._projection(dict(row), principal))
            return visible

    async def get_approval(self, principal: Principal, approval_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            approvals = await self._table(session, "work_approvals")
            links = await self._table(session, "work_approval_workspaces")
            row = (
                (
                    await session.execute(
                        select(approvals).where(
                            approvals.c.approval_id == approval_id,
                            approvals.c.subject_type.in_(self.approval_subject_types),
                            *self._visible(approvals, links, "approval_id", principal),
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise self._not_found()
            if row["requested_action"] is not None:
                await self._material_subject(session, principal, dict(row), mode="read")
            return self._projection(dict(row), principal)

    async def request_approval(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            await self._verify_workspace(session, principal)
            await self._visible_approval_subject(
                session, principal, payload["subject_type"], payload["subject_id"]
            )
            snapshot = None
            if payload["subject_type"] not in self._approval_subjects:
                if not ({"approval.request", "work.write"} & principal.permissions):
                    raise PlatformError(
                        "BUSINESS_APPROVAL_DENIED",
                        "Approval request permission is required.",
                        status_code=403,
                    )
                snapshot = (
                    await self._material_subject(session, principal, payload, mode="request")
                )["snapshot"]
            elif payload.get("requested_action") is not None:
                raise PlatformError(
                    "APPROVAL_SUBJECT_UNSUPPORTED",
                    "Legacy work subjects cannot authorize business actions.",
                    status_code=422,
                )
            approvals = await self._table(session, "work_approvals")
            links = await self._table(session, "work_approval_workspaces")
            approval_id = uuid4().hex
            row = (
                (
                    await session.execute(
                        insert(approvals)
                        .values(
                            approval_id=approval_id,
                            tenant_id=principal.tenant_id,
                            organization_id=principal.organization_id,
                            subject_type=payload["subject_type"],
                            subject_id=payload["subject_id"],
                            requested_action=payload.get("requested_action"),
                            subject_snapshot=snapshot,
                            requested_by=principal.actor_id,
                            approver_actor_id=None,
                            status="PENDING",
                            decision=None,
                            reason=payload.get("reason"),
                            decision_reason=None,
                            materiality_value=payload.get("materiality_value"),
                            requested_at=datetime.now(UTC),
                            decided_at=None,
                        )
                        .returning(approvals)
                    )
                )
                .mappings()
                .one()
            )
            await session.execute(
                insert(links).values(approval_id=approval_id, workspace_id=principal.workspace_id)
            )
            if snapshot is not None:
                await self._audit_material_approval(session, principal, dict(row), "requested")
            return self._projection(dict(row), principal)

    async def decide_approval(
        self,
        principal: Principal,
        approval_id: str,
        *,
        status: str,
        decision: str,
        decision_reason: str | None,
    ) -> tuple[dict[str, Any], bool]:
        async with self._session_factory() as session, session.begin():
            approvals = await self._table(session, "work_approvals")
            links = await self._table(session, "work_approval_workspaces")
            row = await self._locked_record(
                session, approvals, links, "approval_id", approval_id, principal
            )
            if row["subject_type"] not in self.approval_subject_types:
                raise self._not_found()
            if row["requested_by"] == principal.actor_id:
                raise PlatformError(
                    "APPROVAL_SELF_DECISION_DENIED",
                    "Requester cannot decide their own approval.",
                    status_code=403,
                )
            if row["requested_action"] is not None:
                permission = {
                    "APPROVED": "approval.approve",
                    "RETURNED": "approval.return",
                    "REJECTED": "approval.reject",
                    "HELD": "approval.hold",
                }.get(status)
                if permission is None or permission not in principal.permissions:
                    raise PlatformError(
                        "BUSINESS_APPROVAL_DENIED",
                        "Approval decision permission is required.",
                        status_code=403,
                    )
                subject = await self._material_subject(session, principal, row, mode="decide")
                if status == "APPROVED" and subject["snapshot"] != row["subject_snapshot"]:
                    raise PlatformError(
                        "MATERIAL_APPROVAL_STALE",
                        "Business content changed after this request.",
                        status_code=409,
                    )
            normalized_reason = decision_reason.strip() if decision_reason is not None else None
            if not normalized_reason:
                normalized_reason = None
            if status != "APPROVED" and normalized_reason is None:
                raise PlatformError(
                    "APPROVAL_REASON_REQUIRED", "Decision reason is required.", status_code=422
                )
            if row["status"] != "PENDING":
                if (
                    row["status"] == status
                    and row["decision"] == decision
                    and row["approver_actor_id"] == principal.actor_id
                    and row["decision_reason"] == normalized_reason
                ):
                    return self._projection(row, principal), False
                raise PlatformError(
                    "APPROVAL_ALREADY_DECIDED", "Approval is already decided.", status_code=409
                )
            updated = (
                (
                    await session.execute(
                        update(approvals)
                        .where(approvals.c.approval_id == approval_id)
                        .values(
                            status=status,
                            decision=decision,
                            approver_actor_id=principal.actor_id,
                            decision_reason=normalized_reason,
                            decided_at=datetime.now(UTC),
                        )
                        .returning(approvals)
                    )
                )
                .mappings()
                .one()
            )
            if row["requested_action"] is not None:
                await self._audit_material_approval(session, principal, dict(updated), "decided")
            return self._projection(dict(updated), principal), True

    @staticmethod
    def _document_projection(row: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in jsonable_encoder(row).items() if key != "record_id"}

    @staticmethod
    def _document_scope(table: Table, principal: Principal) -> tuple[Any, ...]:
        return (
            table.c.tenant_id == principal.tenant_id,
            table.c.organization_id == principal.organization_id,
            table.c.workspace_id == principal.workspace_id,
        )

    async def _document_row(
        self, session: AsyncSession, principal: Principal, document_id: str, *, lock: bool = False
    ) -> dict[str, Any]:
        documents = await self._table(session, "documents")
        statement = select(documents).where(
            documents.c.document_id == document_id, *self._document_scope(documents, principal)
        )
        if lock:
            statement = statement.with_for_update()
        row = (await session.execute(statement)).mappings().first()
        if row is None:
            raise self._not_found()
        return dict(row)

    async def list_documents(
        self,
        principal: Principal,
        *,
        status: str | None,
        classification: str | None,
        category: str | None,
        search: str | None,
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            documents = await self._table(session, "documents")
            predicates = list(self._document_scope(documents, principal))
            if status:
                predicates.append(documents.c.status == status)
            if classification:
                predicates.append(documents.c.data_classification == classification)
            if category:
                predicates.append(documents.c.category == category)
            if search:
                pattern = f"%{search}%"
                predicates.append(
                    or_(documents.c.title.ilike(pattern), documents.c.category.ilike(pattern))
                )
            rows = (
                (
                    await session.execute(
                        select(documents)
                        .where(*predicates)
                        .order_by(documents.c.created_at.desc(), documents.c.document_id)
                        .limit(200)
                    )
                )
                .mappings()
                .all()
            )
            return [self._document_projection(dict(row)) for row in rows]

    async def get_document(self, principal: Principal, document_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            return self._document_projection(
                await self._document_row(session, principal, document_id)
            )

    async def create_document(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            await self._verify_workspace(session, principal)
            if payload.get("project_id") is not None:
                await self._visible_project_id(session, principal, payload["project_id"])
            documents = await self._table(session, "documents")
            values = dict(payload)
            for key in ("effective_date", "expiry_date"):
                if values.get(key) is not None:
                    values[key] = date.fromisoformat(values[key])
            now = datetime.now(UTC)
            row = (
                (
                    await session.execute(
                        insert(documents)
                        .values(
                            **values,
                            document_id=uuid4().hex,
                            tenant_id=principal.tenant_id,
                            organization_id=principal.organization_id,
                            workspace_id=principal.workspace_id,
                            owner_actor_id=principal.actor_id,
                            status="DRAFT",
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(documents)
                    )
                )
                .mappings()
                .one()
            )
            return self._document_projection(dict(row))

    async def list_document_versions(
        self, principal: Principal, document_id: str
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await self._document_row(session, principal, document_id)
            versions = await self._table(session, "document_versions")
            sources = await self._table(session, "sources")
            actors = await self._table(session, "actors")
            rows = (
                (
                    await session.execute(
                        select(
                            versions,
                            sources.c.title.label("source_title"),
                            actors.c.display_name.label("creator_name"),
                        )
                        .outerjoin(
                            sources,
                            and_(
                                sources.c.source_id == versions.c.source_id,
                                *self._document_scope(sources, principal),
                            ),
                        )
                        .outerjoin(
                            actors,
                            and_(
                                actors.c.actor_id == versions.c.created_by,
                                actors.c.tenant_id == principal.tenant_id,
                                actors.c.organization_id == principal.organization_id,
                            ),
                        )
                        .where(
                            versions.c.document_id == document_id,
                            *self._document_scope(versions, principal),
                        )
                        .order_by(versions.c.created_at.desc(), versions.c.version.desc())
                    )
                )
                .mappings()
                .all()
            )
            return [self._document_projection(dict(row)) for row in rows]

    async def list_document_source_options(
        self, principal: Principal, document_id: str
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            document = await self._document_row(session, principal, document_id)
            if document["status"] != "DRAFT":
                return []
            sources = await self._table(session, "sources")
            versions = await self._table(session, "source_versions")
            rows = (
                (
                    await session.execute(
                        select(
                            sources.c.source_id,
                            sources.c.title.label("source_title"),
                            versions.c.source_version,
                            versions.c.content_hash,
                        )
                        .join(
                            versions,
                            and_(
                                versions.c.source_id == sources.c.source_id,
                                versions.c.tenant_id == sources.c.tenant_id,
                                versions.c.organization_id == sources.c.organization_id,
                                versions.c.workspace_id == sources.c.workspace_id,
                            ),
                        )
                        .where(
                            *self._document_scope(sources, principal),
                            *self._document_scope(versions, principal),
                            or_(
                                sources.c.document_id.is_(None),
                                sources.c.document_id == document_id,
                            ),
                            versions.c.status == "VERIFIED",
                        )
                        .order_by(sources.c.title, versions.c.source_version)
                        .limit(200)
                    )
                )
                .mappings()
                .all()
            )
            return [dict(row) for row in rows]

    async def create_document_version(
        self, principal: Principal, document_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            return await self.create_document_version_in_session(
                session, principal, document_id, payload
            )

    async def create_document_version_in_session(
        self, session: AsyncSession, principal: Principal, document_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        document = await self._document_row(session, principal, document_id, lock=True)
        if document["status"] != "DRAFT":
            raise PlatformError(
                "DOCUMENT_VERSION_FROZEN",
                (
                    "Document versions can only be created when document is in DRAFT status, "
                    f"currently {document['status']}."
                ),
                status_code=409,
            )
        sources = await self._table(session, "sources")
        source_versions = await self._table(session, "source_versions")
        source = (
            (
                await session.execute(
                    select(sources).where(
                        sources.c.source_id == payload["source_id"],
                        *self._document_scope(sources, principal),
                    )
                )
            )
            .mappings()
            .first()
        )
        if source is None or source["document_id"] not in (None, document_id):
            raise self._not_found()
        source_version = (
            (
                await session.execute(
                    select(source_versions).where(
                        source_versions.c.source_id == payload["source_id"],
                        source_versions.c.source_version == payload["source_version"],
                        source_versions.c.status == "VERIFIED",
                        *self._document_scope(source_versions, principal),
                    )
                )
            )
            .mappings()
            .first()
        )
        if source_version is None:
            raise self._not_found()
        versions = await self._table(session, "document_versions")
        existing = await session.execute(
            select(versions.c.record_id).where(
                versions.c.document_id == document_id,
                versions.c.version == payload["version"],
                *self._document_scope(versions, principal),
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise PlatformError(
                "DOCUMENT_VERSION_EXISTS",
                "Document version already exists and cannot be replaced.",
                status_code=409,
            )
        row = (
            (
                await session.execute(
                    insert(versions)
                    .values(
                        tenant_id=principal.tenant_id,
                        organization_id=principal.organization_id,
                        workspace_id=principal.workspace_id,
                        document_id=document_id,
                        version=payload["version"],
                        source_id=payload["source_id"],
                        source_version=payload["source_version"],
                        storage_uri=source_version["storage_uri"],
                        content_hash=source_version["content_hash"],
                        created_by=principal.actor_id,
                        created_at=datetime.now(UTC),
                    )
                    .returning(versions)
                )
            )
            .mappings()
            .one()
        )
        actors = await self._table(session, "actors")
        creator_name = (
            await session.execute(
                select(actors.c.display_name).where(
                    actors.c.actor_id == principal.actor_id,
                    actors.c.tenant_id == principal.tenant_id,
                    actors.c.organization_id == principal.organization_id,
                )
            )
        ).scalar_one_or_none()
        return self._document_projection(
            {
                **dict(row),
                "source_title": source["title"],
                "creator_name": creator_name,
            }
        )

    async def review_document(
        self, principal: Principal, document_id: str
    ) -> tuple[dict[str, Any], bool]:
        async with self._session_factory() as session, session.begin():
            document = await self._document_row(session, principal, document_id, lock=True)
            if document["status"] == "IN_REVIEW":
                return self._document_projection(document), False
            if document["status"] != "DRAFT":
                raise PlatformError(
                    "DOCUMENT_STATUS_CONFLICT",
                    f"Document in status {document['status']} cannot transition to IN_REVIEW.",
                    status_code=409,
                )
            versions = await self._table(session, "document_versions")
            has_version = (
                await session.execute(
                    select(versions.c.record_id)
                    .where(
                        versions.c.document_id == document_id,
                        *self._document_scope(versions, principal),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none() is not None
            if not has_version:
                raise PlatformError(
                    "DOCUMENT_VERSION_REQUIRED",
                    "Document must have at least one version before review.",
                    status_code=409,
                )
            documents = await self._table(session, "documents")
            updated = (
                (
                    await session.execute(
                        update(documents)
                        .where(documents.c.document_id == document_id)
                        .values(status="IN_REVIEW")
                        .returning(documents)
                    )
                )
                .mappings()
                .one()
            )
            return self._document_projection(dict(updated)), True

    async def approve_document(
        self, principal: Principal, document_id: str
    ) -> tuple[dict[str, Any], bool]:
        async with self._session_factory() as session, session.begin():
            document = await self._document_row(session, principal, document_id, lock=True)
            if document["owner_actor_id"] == principal.actor_id:
                raise PlatformError(
                    "DOCUMENT_SELF_APPROVAL_DENIED",
                    "Document owner cannot approve their own document.",
                    status_code=403,
                )
            if document["status"] == "APPROVED":
                return self._document_projection(document), False
            if document["status"] != "IN_REVIEW":
                raise PlatformError(
                    "DOCUMENT_STATUS_CONFLICT",
                    f"Document in status {document['status']} cannot transition to APPROVED.",
                    status_code=409,
                )
            versions = await self._table(session, "document_versions")
            has_version = (
                await session.execute(
                    select(versions.c.record_id)
                    .where(
                        versions.c.document_id == document_id,
                        *self._document_scope(versions, principal),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none() is not None
            if not has_version:
                raise PlatformError(
                    "DOCUMENT_VERSION_REQUIRED",
                    "Document must have at least one immutable version before approval.",
                    status_code=409,
                )
            documents = await self._table(session, "documents")
            updated = (
                (
                    await session.execute(
                        update(documents)
                        .where(documents.c.document_id == document_id)
                        .values(status="APPROVED")
                        .returning(documents)
                    )
                )
                .mappings()
                .one()
            )
            return self._document_projection(dict(updated)), True

    async def retire_document(
        self, principal: Principal, document_id: str
    ) -> tuple[dict[str, Any], bool]:
        async with self._session_factory() as session, session.begin():
            document = await self._document_row(session, principal, document_id, lock=True)
            if document["status"] == "RETIRED":
                return self._document_projection(document), False
            if document["status"] != "APPROVED":
                raise PlatformError(
                    "DOCUMENT_STATUS_CONFLICT",
                    f"Document in status {document['status']} cannot transition to RETIRED.",
                    status_code=409,
                )
            documents = await self._table(session, "documents")
            updated = (
                (
                    await session.execute(
                        update(documents)
                        .where(documents.c.document_id == document_id)
                        .values(status="RETIRED")
                        .returning(documents)
                    )
                )
                .mappings()
                .one()
            )
            return self._document_projection(dict(updated)), True

    async def list_reports(
        self,
        principal: Principal,
        *,
        status: str | None,
        report_type: str | None,
        search: str | None,
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            reports = await self._table(session, "work_reports")
            links = await self._table(session, "work_report_workspaces")
            predicates = list(self._visible(reports, links, "report_id", principal))
            if status:
                predicates.append(reports.c.status == status)
            if report_type:
                predicates.append(reports.c.report_type == report_type)
            if search:
                pattern = f"%{search}%"
                predicates.append(
                    or_(reports.c.title.ilike(pattern), reports.c.report_type.ilike(pattern))
                )
            rows = (
                (
                    await session.execute(
                        select(reports)
                        .where(*predicates)
                        .order_by(reports.c.created_at.desc(), reports.c.report_id)
                        .limit(200)
                    )
                )
                .mappings()
                .all()
            )
            return [self._projection(dict(row), principal) for row in rows]

    async def get_report(self, principal: Principal, report_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            reports = await self._table(session, "work_reports")
            links = await self._table(session, "work_report_workspaces")
            row = (
                (
                    await session.execute(
                        select(reports).where(
                            reports.c.report_id == report_id,
                            *self._visible(reports, links, "report_id", principal),
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise self._not_found()
            return self._projection(dict(row), principal)

    async def create_report(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            await self._verify_workspace(session, principal)
            if payload.get("project_id") is not None:
                await self._visible_project_id(session, principal, payload["project_id"])
            reports = await self._table(session, "work_reports")
            links = await self._table(session, "work_report_workspaces")
            report_id = uuid4().hex
            now = datetime.now(UTC)
            row = (
                (
                    await session.execute(
                        insert(reports)
                        .values(
                            report_id=report_id,
                            tenant_id=principal.tenant_id,
                            organization_id=principal.organization_id,
                            title=payload["title"],
                            report_type=payload["report_type"],
                            description=payload.get("description"),
                            period_start=(
                                date.fromisoformat(payload["period_start"])
                                if payload.get("period_start")
                                else None
                            ),
                            period_end=(
                                date.fromisoformat(payload["period_end"])
                                if payload.get("period_end")
                                else None
                            ),
                            scope=payload.get("scope"),
                            project_id=payload.get("project_id"),
                            status="DRAFT",
                            owner_actor_id=principal.actor_id,
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(reports)
                    )
                )
                .mappings()
                .one()
            )
            await session.execute(
                insert(links).values(
                    report_id=report_id,
                    workspace_id=principal.workspace_id,
                )
            )
            return self._projection(dict(row), principal)

    async def transition_report(
        self, principal: Principal, report_id: str, action: str
    ) -> tuple[dict[str, Any], bool]:
        transitions = {
            "submit_review": ("DRAFT", "IN_REVIEW"),
            "review": ("IN_REVIEW", "APPROVED"),
            "publish": ("APPROVED", "PUBLISHED"),
            "archive": ("PUBLISHED", "ARCHIVED"),
        }
        source, target = transitions[action]
        async with self._session_factory() as session, session.begin():
            reports = await self._table(session, "work_reports")
            links = await self._table(session, "work_report_workspaces")
            current = await self._locked_record(
                session, reports, links, "report_id", report_id, principal
            )
            if current["status"] not in {source, target}:
                raise PlatformError(
                    "REPORT_TRANSITION_INVALID", "Report transition is invalid.", status_code=409
                )
            if action == "submit_review" and current["owner_actor_id"] != principal.actor_id:
                raise PlatformError(
                    "REPORT_OWNER_REQUIRED", "Report ownership is required.", status_code=403
                )
            if action == "review" and current["owner_actor_id"] == principal.actor_id:
                raise PlatformError(
                    "REPORT_SELF_REVIEW_DENIED",
                    "The report owner cannot approve this report.",
                    status_code=403,
                )
            if current["status"] == target:
                return self._projection(current, principal), False
            updated = (
                (
                    await session.execute(
                        update(reports)
                        .where(reports.c.report_id == report_id)
                        .values(
                            status=target,
                            updated_at=datetime.now(UTC),
                            **({"published_at": datetime.now(UTC)} if action == "publish" else {}),
                        )
                        .returning(reports)
                    )
                )
                .mappings()
                .one()
            )
            return self._projection(dict(updated), principal), True

    async def list_report_definitions(self, principal: Principal) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            definitions = await self._table(session, "work_report_definitions")
            actors = await self._table(session, "actors")
            workspaces = await self._table(session, "workspaces")
            rows = (
                (
                    await session.execute(
                        select(
                            definitions,
                            actors.c.display_name.label("owner_name"),
                            workspaces.c.name.label("workspace_name"),
                        )
                        .join(actors, actors.c.actor_id == definitions.c.owner_actor_id)
                        .join(workspaces, workspaces.c.workspace_id == definitions.c.workspace_id)
                        .where(
                            definitions.c.tenant_id == principal.tenant_id,
                            definitions.c.organization_id == principal.organization_id,
                            definitions.c.workspace_id == principal.workspace_id,
                        )
                        .order_by(definitions.c.created_at.desc())
                        .limit(200)
                    )
                )
                .mappings()
                .all()
            )
            return [jsonable_encoder(dict(row)) for row in rows]

    async def get_report_definition(
        self, principal: Principal, definition_id: str
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            return await self._report_definition_row(session, principal, definition_id)

    async def _report_definition_row(
        self,
        session: AsyncSession,
        principal: Principal,
        definition_id: str,
        *,
        lock: bool = False,
    ) -> dict[str, Any]:
        definitions = await self._table(session, "work_report_definitions")
        actors = await self._table(session, "actors")
        workspaces = await self._table(session, "workspaces")
        statement = (
            select(
                definitions,
                actors.c.display_name.label("owner_name"),
                workspaces.c.name.label("workspace_name"),
            )
            .join(actors, actors.c.actor_id == definitions.c.owner_actor_id)
            .join(workspaces, workspaces.c.workspace_id == definitions.c.workspace_id)
            .where(
                definitions.c.report_definition_id == definition_id,
                definitions.c.tenant_id == principal.tenant_id,
                definitions.c.organization_id == principal.organization_id,
                definitions.c.workspace_id == principal.workspace_id,
            )
        )
        if lock:
            statement = statement.with_for_update(of=definitions)
        row = (await session.execute(statement)).mappings().first()
        if row is None:
            raise self._not_found()
        return dict(jsonable_encoder(dict(row)))

    async def create_report_definition(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            await self._verify_workspace(session, principal)
            definitions = await self._table(session, "work_report_definitions")
            definition_id = uuid4().hex
            now = datetime.now(UTC)
            await session.execute(
                insert(definitions).values(
                    report_definition_id=definition_id,
                    tenant_id=principal.tenant_id,
                    organization_id=principal.organization_id,
                    workspace_id=principal.workspace_id,
                    owner_actor_id=principal.actor_id,
                    name=payload["name"],
                    description=payload.get("description"),
                    report_type=payload["report_type"],
                    frequency=payload["frequency"],
                    scope=payload.get("scope"),
                    review_required=payload["review_required"],
                    recipients=payload.get("recipients", []),
                    sections=payload.get("sections", []),
                    data_sources=payload.get("data_sources", []),
                    schedule_config=payload.get("schedule_config", {}),
                    created_at=now,
                    updated_at=now,
                )
            )
            return await self._report_definition_row(session, principal, definition_id)

    async def update_report_definition(
        self, principal: Principal, definition_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            current = await self._report_definition_row(
                session, principal, definition_id, lock=True
            )
            if current["owner_actor_id"] != principal.actor_id:
                raise PlatformError(
                    "REPORT_DEFINITION_OWNER_REQUIRED",
                    "Report definition ownership is required.",
                    status_code=403,
                )
            definitions = await self._table(session, "work_report_definitions")
            await session.execute(
                update(definitions)
                .where(definitions.c.report_definition_id == definition_id)
                .values(**payload, updated_at=datetime.now(UTC))
            )
            return await self._report_definition_row(session, principal, definition_id)

    async def list_findings(
        self,
        principal: Principal,
        *,
        status: str | None,
        severity: str | None,
        source_type: str | None,
        search: str | None,
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            findings = await self._table(session, "work_findings")
            links = await self._table(session, "work_finding_workspaces")
            predicates = list(self._visible(findings, links, "finding_id", principal))
            if status:
                predicates.append(findings.c.status == status)
            if severity:
                predicates.append(findings.c.severity == severity)
            if source_type:
                predicates.append(findings.c.source_type == source_type)
            if search:
                pattern = f"%{search}%"
                predicates.append(
                    or_(
                        findings.c.title.ilike(pattern),
                        findings.c.description.ilike(pattern),
                        findings.c.source_type.ilike(pattern),
                    )
                )
            rows = (
                (
                    await session.execute(
                        select(findings)
                        .where(*predicates)
                        .order_by(findings.c.created_at.desc(), findings.c.finding_id)
                        .limit(200)
                    )
                )
                .mappings()
                .all()
            )
            return [self._projection(dict(row), principal) for row in rows]

    async def get_finding(self, principal: Principal, finding_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            findings = await self._table(session, "work_findings")
            links = await self._table(session, "work_finding_workspaces")
            row = (
                (
                    await session.execute(
                        select(findings).where(
                            findings.c.finding_id == finding_id,
                            *self._visible(findings, links, "finding_id", principal),
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise self._not_found()
            return self._projection(dict(row), principal)

    async def create_finding(self, principal: Principal, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            await self._verify_workspace(session, principal)
            if payload.get("project_id") is not None:
                await self._visible_project_id(session, principal, payload["project_id"])
            if payload.get("corrective_action_task_id") is not None:
                await self._visible_task_id(
                    session, principal, payload["corrective_action_task_id"]
                )
            findings = await self._table(session, "work_findings")
            links = await self._table(session, "work_finding_workspaces")
            finding_id = uuid4().hex
            now = datetime.now(UTC)
            row = (
                (
                    await session.execute(
                        insert(findings)
                        .values(
                            finding_id=finding_id,
                            tenant_id=principal.tenant_id,
                            organization_id=principal.organization_id,
                            title=payload["title"],
                            description=payload.get("description"),
                            severity=payload.get("severity", "MEDIUM"),
                            status="OPEN",
                            source_type="MANUAL",
                            category=payload.get("category"),
                            project_id=payload.get("project_id"),
                            identified_at=now,
                            due_date=(
                                date.fromisoformat(payload["due_date"])
                                if payload.get("due_date")
                                else None
                            ),
                            impact=payload.get("impact"),
                            root_cause=payload.get("root_cause"),
                            corrective_action_task_id=payload.get("corrective_action_task_id"),
                            owner_actor_id=principal.actor_id,
                            created_at=now,
                            updated_at=now,
                        )
                        .returning(findings)
                    )
                )
                .mappings()
                .one()
            )
            await session.execute(
                insert(links).values(
                    finding_id=finding_id,
                    workspace_id=principal.workspace_id,
                )
            )
            return self._projection(dict(row), principal)

    async def update_finding(
        self, principal: Principal, finding_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            findings = await self._table(session, "work_findings")
            links = await self._table(session, "work_finding_workspaces")
            current = await self._locked_record(
                session, findings, links, "finding_id", finding_id, principal
            )
            if current["status"] not in {"OPEN", "ASSIGNED", "IN_PROGRESS"}:
                raise PlatformError(
                    "FINDING_CONTENT_FROZEN", "Finding content is frozen.", status_code=409
                )
            if payload.get("project_id") is not None:
                await self._visible_project_id(session, principal, payload["project_id"])
            if payload.get("corrective_action_task_id") is not None:
                await self._visible_task_id(
                    session, principal, payload["corrective_action_task_id"]
                )
            values = dict(payload)
            if values.get("due_date") is not None:
                values["due_date"] = date.fromisoformat(values["due_date"])
            row = (
                (
                    await session.execute(
                        update(findings)
                        .where(findings.c.finding_id == finding_id)
                        .values(**values, updated_at=datetime.now(UTC))
                        .returning(findings)
                    )
                )
                .mappings()
                .one()
            )
            return self._projection(dict(row), principal)

    async def assign_finding(
        self, principal: Principal, finding_id: str, owner_actor_id: str
    ) -> tuple[dict[str, Any], bool]:
        async with self._session_factory() as session, session.begin():
            findings = await self._table(session, "work_findings")
            links = await self._table(session, "work_finding_workspaces")
            current = await self._locked_record(
                session, findings, links, "finding_id", finding_id, principal
            )
            if current["status"] not in {"OPEN", "ASSIGNED"}:
                raise PlatformError(
                    "FINDING_TRANSITION_INVALID",
                    "Finding cannot be assigned in this state.",
                    status_code=409,
                )
            actors = await self._table(session, "actors")
            memberships = await self._table(session, "workspace_memberships")
            workspaces = await self._table(session, "workspaces")
            now = datetime.now(UTC)
            eligible = await session.execute(
                select(memberships.c.permission_refs)
                .join(memberships, memberships.c.actor_id == actors.c.actor_id)
                .join(workspaces, workspaces.c.workspace_id == memberships.c.workspace_id)
                .where(
                    actors.c.actor_id == owner_actor_id,
                    actors.c.tenant_id == principal.tenant_id,
                    actors.c.organization_id == principal.organization_id,
                    actors.c.active.is_(True),
                    memberships.c.workspace_id == principal.workspace_id,
                    memberships.c.tenant_id == principal.tenant_id,
                    memberships.c.organization_id == principal.organization_id,
                    memberships.c.active.is_(True),
                    memberships.c.revoked_at.is_(None),
                    memberships.c.effective_at <= now,
                    or_(memberships.c.expires_at.is_(None), memberships.c.expires_at > now),
                    workspaces.c.tenant_id == principal.tenant_id,
                    workspaces.c.organization_id == principal.organization_id,
                    workspaces.c.active.is_(True),
                )
            )
            permission_refs = eligible.scalar_one_or_none()
            readable = isinstance(permission_refs, list) and bool(
                {"finding.read", "work.read"}.intersection(permission_refs)
            )
            if not readable:
                raise self._not_found()
            if current["status"] == "ASSIGNED" and current["owner_actor_id"] == owner_actor_id:
                return self._projection(current, principal), False
            row = (
                (
                    await session.execute(
                        update(findings)
                        .where(findings.c.finding_id == finding_id)
                        .values(owner_actor_id=owner_actor_id, status="ASSIGNED", updated_at=now)
                        .returning(findings)
                    )
                )
                .mappings()
                .one()
            )
            return self._projection(dict(row), principal), True

    async def transition_finding(
        self, principal: Principal, finding_id: str, action: str
    ) -> tuple[dict[str, Any], bool]:
        transitions = {
            "start": ({"OPEN", "ASSIGNED"}, "IN_PROGRESS"),
            "submit_verification": ({"IN_PROGRESS"}, "PENDING_VERIFICATION"),
            "verify": ({"PENDING_VERIFICATION"}, "VERIFIED"),
            "close": ({"VERIFIED"}, "CLOSED"),
        }
        allowed, target = transitions[action]
        async with self._session_factory() as session, session.begin():
            findings = await self._table(session, "work_findings")
            links = await self._table(session, "work_finding_workspaces")
            current = await self._locked_record(
                session, findings, links, "finding_id", finding_id, principal
            )
            if current["status"] not in allowed and current["status"] != target:
                raise PlatformError(
                    "FINDING_TRANSITION_INVALID",
                    "Finding transition is invalid.",
                    status_code=409,
                )
            if action in {"start", "submit_verification"} and (
                current["owner_actor_id"] != principal.actor_id
            ):
                raise PlatformError(
                    "FINDING_OWNER_REQUIRED", "Finding ownership is required.", status_code=403
                )
            if action == "verify" and current["owner_actor_id"] == principal.actor_id:
                raise PlatformError(
                    "FINDING_SELF_VERIFICATION_DENIED",
                    "The finding owner cannot verify this finding.",
                    status_code=403,
                )
            if current["status"] == target:
                return self._projection(current, principal), False
            row = (
                (
                    await session.execute(
                        update(findings)
                        .where(findings.c.finding_id == finding_id)
                        .values(
                            status=target,
                            updated_at=datetime.now(UTC),
                            **(
                                {
                                    "verifier_actor_id": principal.actor_id,
                                    "verified_at": datetime.now(UTC),
                                }
                                if action == "verify"
                                else {}
                            ),
                        )
                        .returning(findings)
                    )
                )
                .mappings()
                .one()
            )
            return self._projection(dict(row), principal), True
