"""Small scoped SQL/audit primitives; business rules stay in their owner services."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import MetaData, Numeric, Table, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.dml import ReturningInsert, ReturningUpdate

from alos.audit import AuditEvent, SqlAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.identity import DataScope, Principal
from alos.observability.correlation import current_correlation_id
from alos.security.errors import PlatformError


@dataclass(frozen=True)
class RecordSpec:
    table: str
    identifier: str
    name: str
    initial: str | None
    transitions: dict[str, tuple[str, ...]]
    create_fields: frozenset[str]
    update_fields: frozenset[str]
    immutable: bool = False
    enrich_projection: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def authorize(principal: Principal, domain: str, action: str, *, executive: bool = False) -> None:
    if not principal.active or not principal.workspace_id:
        raise PlatformError(
            "BUSINESS_AUTHORITY_DENIED", "Active workspace is required.", status_code=403
        )
    if executive:
        allowed = "EXECUTIVE" in principal.roles and "strategy.read" in principal.permissions
    else:
        allowed = (
            bool(principal.roles & {"DIVISION_LEAD", "DIVISION_MEMBER"})
            and f"{domain}.{action}" in principal.permissions
        )
    if not allowed:
        raise PlatformError(
            "BUSINESS_AUTHORITY_DENIED", "Domain permission is required.", status_code=403
        )


def conflict(message: str) -> PlatformError:
    return PlatformError("BUSINESS_STATE_CONFLICT", message, status_code=409)


class RecordRepository:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        contracts: CanonicalContractCatalog | None = None,
    ) -> None:
        self.factory = factory
        self.contracts = contracts
        self.audit = SqlAuditRepository(factory)
        self._metadata = MetaData()
        self._tables: dict[tuple[str, str], Table] = {}
        self._lock = asyncio.Lock()

    async def table(self, session: AsyncSession, schema: str, name: str) -> Table:
        key = (schema, name)
        if key not in self._tables:
            async with self._lock:
                if key not in self._tables:
                    connection = await session.connection()
                    self._tables[key] = await connection.run_sync(
                        lambda conn: Table(name, self._metadata, schema=schema, autoload_with=conn)
                    )
        return self._tables[key]

    @staticmethod
    def scope(table: Table, principal: Principal, *, company: bool = False) -> tuple[Any, ...]:
        conditions = (
            table.c.tenant_id == principal.tenant_id,
            table.c.organization_id == principal.organization_id,
        )
        return (
            conditions
            if company and principal.data_scope == DataScope.COMPANY
            else (
                *conditions,
                table.c.workspace_id == principal.workspace_id,
            )
        )

    async def workspace(self, session: AsyncSession, principal: Principal) -> None:
        table = await self.table(session, "core", "workspaces")
        found = await session.scalar(
            select(table.c.workspace_id).where(
                *self.scope(table, principal),
                table.c.active.is_(True),
            )
        )
        if found is None:
            raise PlatformError(
                "WORKSPACE_ACCESS_DENIED", "Active workspace is unavailable.", status_code=403
            )

    async def row(
        self,
        session: AsyncSession,
        schema: str,
        spec: RecordSpec,
        principal: Principal,
        identity: str,
        *,
        lock: bool = False,
    ) -> dict[str, Any]:
        table = await self.table(session, schema, spec.table)
        query = select(table).where(
            *self.scope(table, principal), table.c[spec.identifier] == identity
        )
        if lock:
            query = query.with_for_update()
        result = (await session.execute(query)).mappings().first()
        if result is None:
            raise PlatformError(
                "BUSINESS_RECORD_NOT_FOUND", "Record is not visible.", status_code=404
            )
        return dict(result)

    @staticmethod
    def values(table: Table, payload: dict[str, Any]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for key, value in payload.items():
            if value is None:
                values[key] = None
                continue
            kind = table.c[key].type
            python_type = kind.python_type
            try:
                if python_type is Decimal:
                    if not isinstance(value, str):
                        raise ValueError("Exact decimal string required")
                    number = Decimal(value)
                    numeric = cast(Numeric[Decimal], kind)
                    scale = numeric.scale or 0
                    precision = numeric.precision or 20
                    if (
                        not number.is_finite()
                        or int(number.as_tuple().exponent) < -scale
                        or abs(number) >= Decimal(10) ** (precision - scale)
                    ):
                        raise ValueError("Invalid amount or scale")
                    values[key] = number
                elif python_type is datetime:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        raise ValueError("Timezone is required")
                    values[key] = parsed
                elif python_type is date:
                    values[key] = date.fromisoformat(value)
                else:
                    values[key] = value
            except (ValueError, TypeError, InvalidOperation) as exc:
                raise PlatformError(
                    "BUSINESS_VALUE_INVALID", "Value is invalid.", status_code=422
                ) from exc
        return values

    @staticmethod
    def editable(spec: RecordSpec, payload: dict[str, Any], *, creating: bool) -> None:
        allowed = spec.create_fields if creating else spec.update_fields
        if set(payload) - allowed or (not creating and not payload):
            raise PlatformError(
                "BUSINESS_FIELDS_INVALID", "Unknown or protected fields.", status_code=422
            )

    def known_lifecycle(self, schema: str, spec: RecordSpec, row: dict[str, Any]) -> None:
        if spec.initial is None:
            return
        if self.contracts is None:
            raise PlatformError(
                "CONTRACTS_UNAVAILABLE", "Canonical contract is unavailable.", status_code=503
            )
        try:
            states = self.contracts.enum_values(
                f"https://schemas.alos.dev/v1/{schema}/{schema}-contracts.schema.json",
                spec.name + "Status",
            )
        except ValueError as exc:
            raise PlatformError(
                "CONTRACTS_UNAVAILABLE", "Canonical lifecycle is unavailable.", status_code=503
            ) from exc
        if row.get("status") not in states:
            raise conflict("Unrecognized historical lifecycle cannot be mutated or referenced.")

    async def write(
        self,
        session: AsyncSession,
        schema: str,
        spec: RecordSpec,
        principal: Principal,
        values: dict[str, Any],
        identity: str | None,
        *,
        operation: str,
    ) -> dict[str, Any]:
        table = await self.table(session, schema, spec.table)
        now = datetime.now(UTC)
        values = {**values, "updated_at": now}
        if identity is None:
            identity = uuid4().hex
            values.update(
                {
                    spec.identifier: identity,
                    "created_at": now,
                    "tenant_id": principal.tenant_id,
                    "organization_id": principal.organization_id,
                    "workspace_id": principal.workspace_id,
                }
            )
            if spec.initial is not None:
                values["status"] = spec.initial
            query: ReturningInsert[Any] | ReturningUpdate[Any] = (
                insert(table).values(**values).returning(table)
            )
        else:
            query = (
                update(table)
                .where(*self.scope(table, principal), table.c[spec.identifier] == identity)
                .values(**values)
                .returning(table)
            )
        row = dict((await session.execute(query)).mappings().one())
        await self.audit.append_in_session(
            session,
            AuditEvent(
                event_type=f"{schema}.{spec.table}.{operation}",
                entity_type=spec.table,
                entity_id=identity,
                tenant_id=principal.tenant_id,
                organization_id=principal.organization_id,
                workspace_id=principal.workspace_id,
                actor_id=principal.actor_id,
                correlation_id=current_correlation_id(),
                outcome="SUCCEEDED",
                occurred_at=now,
                reason="Canonical business record mutation",
                metadata={"operation": operation},
            ),
        )
        projection = self.project(spec, row)
        if self.contracts is None:
            raise PlatformError(
                "CONTRACTS_UNAVAILABLE", "Canonical contract is unavailable.", status_code=503
            )
        self.contracts.validate(
            f"https://schemas.alos.dev/v1/{schema}/{schema}-contracts.schema.json#/$defs/{spec.name}Projection",
            projection,
        )
        return projection

    @staticmethod
    def project(spec: RecordSpec, row: dict[str, Any]) -> dict[str, Any]:
        result = {
            key: (
                format(value, "f")
                if isinstance(value, Decimal)
                else value.isoformat()
                if isinstance(value, (date, datetime))
                else value
            )
            for key, value in row.items()
        }
        result["allowed_transitions"] = list(spec.transitions.get(str(row.get("status")), ()))
        if spec.enrich_projection is not None:
            result.update(spec.enrich_projection(row))
        return result

    async def listing(
        self, schema: str, spec: RecordSpec, principal: Principal, limit: int, offset: int
    ) -> dict[str, Any]:
        async with self.factory() as session, session.begin():
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            await self.workspace(session, principal)
            table = await self.table(session, schema, spec.table)
            where = self.scope(table, principal)
            aggregate = (
                await session.execute(
                    select(func.count(), func.max(table.c.updated_at)).where(*where)
                )
            ).one()
            rows = (
                (
                    await session.execute(
                        select(table)
                        .where(*where)
                        .order_by(table.c.updated_at.desc(), table.c[spec.identifier])
                        .limit(limit)
                        .offset(offset)
                    )
                )
                .mappings()
                .all()
            )
            return {
                "items": [self.project(spec, dict(row)) for row in rows],
                "total": aggregate[0],
                "source": self.source(schema, aggregate[0], aggregate[1]),
            }

    async def detail(
        self, schema: str, spec: RecordSpec, principal: Principal, identity: str
    ) -> dict[str, Any]:
        async with self.factory() as session, session.begin():
            await self.workspace(session, principal)
            return self.project(spec, await self.row(session, schema, spec, principal, identity))

    async def mutate(
        self,
        schema: str,
        spec: RecordSpec,
        principal: Principal,
        payload: dict[str, Any],
        identity: str | None,
        operation: str,
        rule: Callable[
            [AsyncSession, RecordSpec, Principal, dict[str, Any], dict[str, Any] | None, str],
            Awaitable[None],
        ],
        before_lock: Callable[
            [AsyncSession, RecordSpec, Principal, dict[str, Any], dict[str, Any] | None, str],
            Awaitable[None],
        ]
        | None = None,
    ) -> dict[str, Any]:
        """One state/audit transaction; each owner supplies its business policy."""
        if operation != "transition":
            self.editable(spec, payload, creating=identity is None)
        async with self.factory() as session, session.begin():
            await self.workspace(session, principal)
            table = await self.table(session, schema, spec.table)
            values = self.values(table, payload)
            if before_lock is not None:
                snapshot = (
                    None
                    if identity is None
                    else await self.row(session, schema, spec, principal, identity)
                )
                await before_lock(session, spec, principal, values, snapshot, operation)
            old = (
                None
                if identity is None
                else await self.row(session, schema, spec, principal, identity, lock=True)
            )
            if old is not None:
                self.known_lifecycle(schema, spec, old)
            if old is not None and spec.immutable:
                raise conflict("Historical records are immutable.")
            if operation == "transition":
                if old is None or values.get("status") not in spec.transitions.get(
                    old["status"], ()
                ):
                    raise conflict("Lifecycle transition is unavailable.")
            await rule(session, spec, principal, values, old, operation)
            return await self.write(
                session, schema, spec, principal, values, identity, operation=operation
            )

    @staticmethod
    def source(domain: str, count: int, updated: datetime | None) -> dict[str, Any]:
        return {
            "source": domain,
            "status": "CONNECTED" if count else "CONNECTED_EMPTY",
            "authoritative": True,
            "last_updated_at": updated.isoformat() if updated else None,
        }

    async def summary(
        self,
        schema: str,
        specs: dict[str, RecordSpec],
        principal: Principal,
        *,
        company: bool = False,
    ) -> dict[str, Any]:
        counts: dict[str, int] = {}
        timestamps: list[datetime] = []
        async with self.factory() as session, session.begin():
            await session.execute(
                text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            )
            await self.workspace(session, principal)
            for name in specs:
                table = await self.table(session, schema, name)
                count, stamp = (
                    await session.execute(
                        select(func.count(), func.max(table.c.updated_at)).where(
                            *self.scope(table, principal, company=company)
                        )
                    )
                ).one()
                counts[name] = count
                if stamp is not None:
                    timestamps.append(stamp)
        updated = max(timestamps) if timestamps else None
        projection = {
            "source": self.source(schema, sum(counts.values()), updated),
            "counts": counts,
            "last_updated_at": updated.isoformat() if updated else None,
        }
        if self.contracts is None:
            raise PlatformError(
                "CONTRACTS_UNAVAILABLE", "Canonical contract is unavailable.", status_code=503
            )
        try:
            self.contracts.validate(
                f"https://schemas.alos.dev/v1/{schema}/{schema}-contracts.schema.json", projection
            )
        except ValueError as exc:
            raise PlatformError(
                "CONTRACTS_UNAVAILABLE", "Canonical projection is unavailable.", status_code=503
            ) from exc
        return projection
