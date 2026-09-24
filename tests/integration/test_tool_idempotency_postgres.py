from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alos.authorization import AuthorizationPolicy
from alos.identity import Principal
from alos.tools.executor.service import (
    InMemoryToolAuditSink,
    SqlToolIdempotencyStore,
    ToolExecutor,
)
from alos.tools.registry import IdempotencyPolicy, ToolRegistration, ToolRegistry


def _database_url(name: str) -> str:
    source = os.environ.get(
        "ALOS_TEST_DATABASE_URL",
        "postgresql+asyncpg://alos:alos@127.0.0.1:5432/alos_test",
    )
    parts = urlsplit(source)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, parts.fragment))


async def _database(name: str) -> str:
    url = _database_url(name)
    admin = await asyncpg.connect(_database_url("postgres").replace("+asyncpg", ""))
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
    )
    return url


class AcceptingContractValidator:
    def validate_request(self, payload: Mapping[str, Any]) -> None:
        assert payload["tool_call_id"]

    def validate_result(self, payload: Mapping[str, Any]) -> None:
        assert payload["correlation_id"]


class SlowCountingAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def validate_arguments(self, _arguments: Mapping[str, Any]) -> None:
        return None

    async def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        execution_context: Mapping[str, Any],
    ) -> Any:
        del execution_context
        await asyncio.sleep(0.05)
        self.calls += 1
        return {"value": arguments["message"], "calls": self.calls}


def _request() -> dict[str, Any]:
    return {
        "tool_call_id": "toolcall_pg_idempotency_001",
        "run_id": "run_pg_idempotency_001",
        "tool_id": "diagnostic.idempotent",
        "execution_context": {
            "tenant_id": "tenant_pg_idempotency",
            "organization_id": "org_pg_idempotency",
            "workspace_id": "workspace_pg_idempotency",
            "actor_id": "actor_pg_idempotency",
            "permission_refs": ["tools.idempotent.execute"],
            "scope_refs": ["scope.idempotent"],
            "correlation_id": "corr_pg_idempotency_001",
        },
        "arguments": {"message": "execute once"},
        "idempotency_key": "idempotency-pg-concurrent-001",
    }


def _principal() -> Principal:
    return Principal(
        actor_id="actor_pg_idempotency",
        tenant_id="tenant_pg_idempotency",
        organization_id="org_pg_idempotency",
        workspace_id="workspace_pg_idempotency",
        permissions=frozenset({"tools.idempotent.execute"}),
        scopes=frozenset({"scope.idempotent"}),
    )


@pytest.mark.asyncio
async def test_postgres_idempotency_serializes_concurrent_side_effects() -> None:
    url = await _database("alos_tool_idempotency_concurrency")
    engine = create_async_engine(url)
    adapter = SlowCountingAdapter()
    registry = ToolRegistry()
    registry.register(
        ToolRegistration(
            tool_id="diagnostic.idempotent",
            required_permission="tools.idempotent.execute",
            required_scopes=frozenset({"scope.idempotent"}),
            adapter=adapter,
            idempotency_policy=IdempotencyPolicy.REQUIRED,
        )
    )
    executor = ToolExecutor(
        contract_validator=AcceptingContractValidator(),
        authorization=AuthorizationPolicy(),
        registry=registry,
        audit_sink=InMemoryToolAuditSink(),
        idempotency_store=SqlToolIdempotencyStore(
            async_sessionmaker(engine, expire_on_commit=False)
        ),
        production=False,
    )
    try:
        first, second = await asyncio.gather(
            executor.execute(_request(), principal=_principal()),
            executor.execute(_request(), principal=_principal()),
        )
    finally:
        await engine.dispose()

    assert first.result["status"] == "SUCCESS"
    assert second.result["status"] == "SUCCESS"
    assert first.result["output"] == second.result["output"]
    assert adapter.calls == 1
