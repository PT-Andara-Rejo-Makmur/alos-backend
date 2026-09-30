"""PostgreSQL authority for workspace-visible projects and tasks."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

from fastapi.encoders import jsonable_encoder
from sqlalchemy import MetaData, Table, exists, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.identity import Principal
from alos.security.errors import PlatformError


class SharedWorkService:
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
                projects = await self._table(session, "projects")
                project_links = await self._table(session, "project_workspaces")
                visible = await session.execute(
                    select(projects.c.project_id).where(
                        projects.c.project_id == project_id,
                        *self._visible(projects, project_links, "project_id", principal),
                    )
                )
                if visible.scalar_one_or_none() is None:
                    raise self._not_found()
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
