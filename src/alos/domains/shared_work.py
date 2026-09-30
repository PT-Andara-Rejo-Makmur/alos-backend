"""PostgreSQL authority for workspace-visible projects and tasks."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any, ClassVar
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import MetaData, Table, exists, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.identity import Principal
from alos.security.errors import PlatformError


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

    async def _table(self, session: AsyncSession, name: str) -> Table:
        cached = self._tables.get(name)
        if cached is not None:
            return cached
        async with self._reflection_lock:
            cached = self._tables.get(name)
            if cached is not None:
                return cached
            connection = await session.connection()
            table = await connection.run_sync(
                lambda sync_connection: Table(
                    name, self._metadata, schema="core", autoload_with=sync_connection
                )
            )
            self._tables[name] = table
            return table

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
        return {**jsonable_encoder(row), "workspace_ids": [principal.workspace_id]}

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
                "owner_actor_id": None,
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

    async def _visible_approval_subject(
        self, session: AsyncSession, principal: Principal, subject_type: str, subject_id: str
    ) -> None:
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
            predicates.append(approvals.c.subject_type.in_(self._approval_subjects))
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
            return [self._projection(dict(row), principal) for row in rows]

    async def get_approval(self, principal: Principal, approval_id: str) -> dict[str, Any]:
        async with self._session_factory() as session:
            approvals = await self._table(session, "work_approvals")
            links = await self._table(session, "work_approval_workspaces")
            row = (
                (
                    await session.execute(
                        select(approvals).where(
                            approvals.c.approval_id == approval_id,
                            approvals.c.subject_type.in_(self._approval_subjects),
                            *self._visible(approvals, links, "approval_id", principal),
                        )
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise self._not_found()
            return self._projection(dict(row), principal)

    async def request_approval(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            await self._verify_workspace(session, principal)
            await self._visible_approval_subject(
                session, principal, payload["subject_type"], payload["subject_id"]
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
                            requested_by=principal.actor_id,
                            approver_actor_id=None,
                            status="PENDING",
                            decision=None,
                            reason=payload.get("reason"),
                            decision_reason=None,
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
            if row["subject_type"] not in self._approval_subjects:
                raise self._not_found()
            if row["requested_by"] == principal.actor_id:
                raise PlatformError(
                    "APPROVAL_SELF_DECISION_DENIED",
                    "Requester cannot decide their own approval.",
                    status_code=403,
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
        self, principal: Principal, *, status: str | None, classification: str | None,
        category: str | None, search: str | None,
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
            documents = await self._table(session, "documents")
            row = (
                (
                    await session.execute(
                        insert(documents)
                        .values(
                            **payload,
                            document_id=uuid4().hex,
                            tenant_id=principal.tenant_id,
                            organization_id=principal.organization_id,
                            workspace_id=principal.workspace_id,
                            owner_actor_id=principal.actor_id,
                            status="DRAFT",
                            created_at=datetime.now(UTC),
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
            rows = (
                (
                    await session.execute(
                        select(versions)
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

    async def create_document_version(
        self, principal: Principal, document_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
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
            return self._document_projection(dict(row))

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

    async def create_report(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            await self._verify_workspace(session, principal)
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

    async def create_finding(
        self, principal: Principal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            await self._verify_workspace(session, principal)
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
            row = (
                (
                    await session.execute(
                        update(findings)
                        .where(findings.c.finding_id == finding_id)
                        .values(**payload, updated_at=datetime.now(UTC))
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
                    "FINDING_TRANSITION_INVALID", "Finding cannot be assigned in this state.",
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
                    "FINDING_TRANSITION_INVALID", "Finding transition is invalid.",
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
                    "The finding owner cannot verify this finding.", status_code=403,
                )
            if current["status"] == target:
                return self._projection(current, principal), False
            row = (
                (
                    await session.execute(
                        update(findings)
                        .where(findings.c.finding_id == finding_id)
                        .values(status=target, updated_at=datetime.now(UTC))
                        .returning(findings)
                    )
                )
                .mappings()
                .one()
            )
            return self._projection(dict(row), principal), True


