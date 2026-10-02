"""SQL conversation authority: every lookup joins the full authenticated boundary."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.ara.models import AraMessageRecord, AraRunRecord, AraThreadRecord
from alos.identity import Principal
from alos.persistence.models import ReviewPackageRecord
from alos.research.persistence import persist_result
from alos.security.errors import PlatformError


def project(row: Any) -> dict[str, Any]:
    payload = {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name != "active_run_id"
    }
    return {
        key: value.isoformat() if isinstance(value, datetime) else value
        for key, value in payload.items()
        if value is not None
    }


class AraRepository:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self.factory = factory

    @staticmethod
    def boundary(principal: Principal) -> tuple[Any, ...]:
        return tuple(
            getattr(AraThreadRecord, key) == getattr(principal, key)
            for key in ("tenant_id", "organization_id", "workspace_id", "actor_id")
        )

    async def _thread(
        self, session: AsyncSession, principal: Principal, thread_id: str, *, lock: bool = False
    ) -> AraThreadRecord:
        query = select(AraThreadRecord).where(
            *self.boundary(principal), AraThreadRecord.thread_id == thread_id
        )
        row = await session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise PlatformError(
                "ARA_THREAD_NOT_FOUND", "Percakapan tidak tersedia.", status_code=404
            )
        return row

    async def create(self, principal: Principal, title: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        row = AraThreadRecord(
            thread_id=f"thread_{uuid4().hex}",
            **{
                key: getattr(principal, key)
                for key in ("tenant_id", "organization_id", "workspace_id", "actor_id")
            },
            title=title,
            status="ACTIVE",
            active_run_id=None,
            created_at=now,
            updated_at=now,
        )
        async with self.factory.begin() as session:
            session.add(row)
        return project(row)

    async def list_threads(self, principal: Principal) -> list[dict[str, Any]]:
        async with self.factory() as session:
            rows = await session.scalars(
                select(AraThreadRecord)
                .where(*self.boundary(principal))
                .order_by(AraThreadRecord.updated_at.desc())
                .limit(100)
            )
            return [project(row) for row in rows]

    async def get(self, principal: Principal, thread_id: str) -> dict[str, Any]:
        async with self.factory() as session:
            return project(await self._thread(session, principal, thread_id))

    async def messages(self, principal: Principal, thread_id: str) -> list[dict[str, Any]]:
        async with self.factory() as session:
            await self._thread(session, principal, thread_id)
            rows = await session.scalars(
                select(AraMessageRecord)
                .where(AraMessageRecord.thread_id == thread_id)
                .order_by(AraMessageRecord.created_at.desc(), AraMessageRecord.message_id.desc())
                .limit(500)
            )
            return [project(row) for row in reversed(list(rows))]

    async def reserve(
        self,
        principal: Principal,
        thread_id: str,
        message: str,
        run_id: str,
        correlation_id: str,
        runtime_mode: str = "DETERMINISTIC_TEST",
    ) -> None:
        async with self.factory.begin() as session:
            thread = await self._thread(session, principal, thread_id, lock=True)
            now = datetime.now(UTC)
            if thread.active_run_id:
                previous = await session.get(AraRunRecord, thread.active_run_id)
                if previous and previous.created_at > now - timedelta(minutes=2):
                    raise PlatformError(
                        "ARA_RUN_CONFLICT", "Percakapan masih diproses.", status_code=409
                    )
                if previous:
                    previous.status = "TIMED_OUT"
            thread.active_run_id = run_id
            thread.updated_at = now
            if thread.title == "Percakapan ARA":
                thread.title = message[:80]
            session.add(
                AraRunRecord(
                    run_id=run_id,
                    thread_id=thread_id,
                    correlation_id=correlation_id,
                    status="RUNNING",
                    runtime_mode=runtime_mode,
                    created_at=now,
                )
            )
            session.add(
                AraMessageRecord(
                    message_id=f"message_{uuid4().hex}",
                    thread_id=thread_id,
                    role="USER",
                    content=message,
                    run_id=run_id,
                    correlation_id=correlation_id,
                    created_at=now,
                )
            )

    async def finish(
        self,
        principal: Principal,
        thread_id: str,
        run_id: str,
        response: dict[str, Any],
        status: str,
    ) -> dict[str, Any]:
        async with self.factory.begin() as session:
            thread = await self._thread(session, principal, thread_id, lock=True)
            run = await session.get(AraRunRecord, run_id)
            if run is None or run.thread_id != thread_id or thread.active_run_id != run_id:
                raise PlatformError("ARA_RUN_CONFLICT", "Run tidak lagi aktif.", status_code=409)
            run.status, run.response = status, response
            now = datetime.now(UTC)
            session.add(
                AraMessageRecord(
                    message_id=f"message_{uuid4().hex}",
                    thread_id=thread_id,
                    role="ASSISTANT",
                    content=response["answer"],
                    run_id=run_id,
                    correlation_id=run.correlation_id,
                    created_at=now,
                    response=response,
                )
            )
            thread.active_run_id, thread.updated_at = None, now
        return project(run)

    async def run(self, principal: Principal, thread_id: str, run_id: str) -> dict[str, Any]:
        async with self.factory() as session:
            await self._thread(session, principal, thread_id)
            row = await session.get(AraRunRecord, run_id)
            if row is None or row.thread_id != thread_id:
                raise PlatformError("ARA_RUN_NOT_FOUND", "Run tidak tersedia.", status_code=404)
            return project(row)

    async def persist_advisory(
        self, principal: Principal, response: dict[str, Any], contract_version: str
    ) -> None:
        if response.get("research_result"):
            await persist_result(self.factory, principal, response["research_result"])
        package = response.get("review_package")
        if package:
            identity = package["identity"]
            async with self.factory.begin() as session:
                session.add(
                    ReviewPackageRecord(
                        review_id=identity["review_id"],
                        tenant_id=principal.tenant_id,
                        workspace_id=principal.workspace_id,
                        subject_id=identity["subject_id"],
                        subject_version=identity["subject_version"],
                        contract_version=contract_version,
                        evidence_uri=f"urn:alos:review-package:{identity['review_id']}",
                        recorded_at=datetime.now(UTC),
                    )
                )
