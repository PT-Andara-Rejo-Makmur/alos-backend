"""Authoritative ToolExecutor pipeline."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Set
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alos.authorization import AuthorizationPolicy
from alos.context.policy import maximum_classification
from alos.evidence import resolve_registry_result
from alos.identity import Principal
from alos.persistence.models import ToolIdempotencyRecord
from alos.tools.adapters.base import ToolInputError
from alos.tools.business.catalog import BUSINESS_TOOLS, business_tool_allowed
from alos.tools.contracts import ToolContractValidator
from alos.tools.registry import (
    IdempotencyPolicy,
    ToolLifecycleState,
    ToolRegistration,
    ToolRegistry,
)


@dataclass(frozen=True, slots=True)
class ToolExecutionOutcome:
    correlation_id: str
    result: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolAuditRecord:
    tool_call_id: str
    run_id: str
    tool_id: str
    tenant_id: str
    organization_id: str
    workspace_id: str
    actor_id: str
    correlation_id: str
    outcome: str
    occurred_at: datetime


class ToolAuditSink(Protocol):
    async def append(self, record: ToolAuditRecord) -> None: ...


class InMemoryToolAuditSink:
    """Test/local sink. Production composition must provide the SQL audit repository."""

    def __init__(self) -> None:
        self.records: list[ToolAuditRecord] = []

    async def append(self, record: ToolAuditRecord) -> None:
        self.records.append(record)


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    request_digest: str
    output: Any


class InMemoryToolIdempotencyStore:
    """Process-local baseline; production persistence is owned by Backend."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str], IdempotencyRecord] = {}
        self._lock = asyncio.Lock()
        self._key_locks: dict[tuple[str, str, str], asyncio.Lock] = {}

    @asynccontextmanager
    async def serialize(self, key: tuple[str, str, str]) -> AsyncIterator[None]:
        async with self._lock:
            lock = self._key_locks.setdefault(key, asyncio.Lock())
        async with lock:
            yield

    async def get(self, key: tuple[str, str, str]) -> IdempotencyRecord | None:
        async with self._lock:
            record = self._records.get(key)
            return copy.deepcopy(record)

    async def put(
        self,
        key: tuple[str, str, str],
        record: IdempotencyRecord,
    ) -> None:
        async with self._lock:
            existing = self._records.get(key)
            if existing is not None and existing.request_digest != record.request_digest:
                raise ValueError("idempotency key already belongs to another request")
            self._records[key] = copy.deepcopy(record)


class ToolIdempotencyStore(Protocol):
    def serialize(self, key: tuple[str, str, str]) -> AbstractAsyncContextManager[None]: ...

    async def get(self, key: tuple[str, str, str]) -> IdempotencyRecord | None: ...

    async def put(self, key: tuple[str, str, str], record: IdempotencyRecord) -> None: ...


class SqlToolIdempotencyStore:
    """Persistent Backend-owned idempotency boundary for business tool execution."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    @asynccontextmanager
    async def serialize(self, key: tuple[str, str, str]) -> AsyncIterator[None]:
        lock_seed = "\x1f".join(key).encode("utf-8")
        lock_id = int.from_bytes(hashlib.sha256(lock_seed).digest()[:8], "big", signed=True)
        async with self._session_factory.begin() as session:
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": lock_id},
            )
            yield

    async def get(self, key: tuple[str, str, str]) -> IdempotencyRecord | None:
        tenant_id, tool_id, idempotency_key = key
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ToolIdempotencyRecord).where(
                        ToolIdempotencyRecord.tenant_id == tenant_id,
                        ToolIdempotencyRecord.tool_id == tool_id,
                        ToolIdempotencyRecord.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return IdempotencyRecord(request_digest=row.request_digest, output=row.output)

    async def put(self, key: tuple[str, str, str], record: IdempotencyRecord) -> None:
        tenant_id, tool_id, idempotency_key = key
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(ToolIdempotencyRecord).where(
                        ToolIdempotencyRecord.tenant_id == tenant_id,
                        ToolIdempotencyRecord.tool_id == tool_id,
                        ToolIdempotencyRecord.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                if row.request_digest != record.request_digest:
                    raise ValueError("idempotency key already belongs to another request")
                return
            session.add(
                ToolIdempotencyRecord(
                    tenant_id=tenant_id,
                    tool_id=tool_id,
                    idempotency_key=idempotency_key,
                    request_digest=record.request_digest,
                    output=record.output,
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()


class ToolExecutor:
    """Deny-by-default execution boundary owned exclusively by ALOS Backend."""

    def __init__(
        self,
        *,
        contract_validator: ToolContractValidator,
        authorization: AuthorizationPolicy,
        registry: ToolRegistry,
        audit_sink: ToolAuditSink,
        idempotency_store: ToolIdempotencyStore | None = None,
        production: bool,
        evidence_registry: Any = None,
    ) -> None:
        self._contract_validator = contract_validator
        self._authorization = authorization
        self._registry = registry
        self._audit_sink = audit_sink
        self._idempotency_store = idempotency_store or InMemoryToolIdempotencyStore()
        self._production = production
        self._evidence_registry = evidence_registry

    async def execute(
        self,
        request: Mapping[str, Any],
        *,
        principal: Principal | None,
        transport_correlation_id: str | None = None,
        authorized_tool_ids: Set[str] | None = None,
    ) -> ToolExecutionOutcome:
        self._contract_validator.validate_request(request)
        context = request["execution_context"]
        assert isinstance(context, Mapping)
        correlation_id = str(context["correlation_id"])
        await self._audit(request, correlation_id, "REQUESTED")

        if transport_correlation_id is not None and transport_correlation_id != correlation_id:
            return await self._finish_error(
                request,
                correlation_id,
                status="DENIED",
                code="CORRELATION_MISMATCH",
                message="Transport and ToolRequest correlation identifiers must match.",
            )

        tool_id = str(request["tool_id"])
        if authorized_tool_ids is not None and tool_id not in authorized_tool_ids:
            return await self._finish_error(
                request,
                correlation_id,
                status="DENIED",
                code="TOOL_NOT_AUTHORIZED_FOR_RUN",
                message="The authoritative run authorization does not allow this tool.",
            )

        registration = self._registry.get(tool_id)
        if registration is None:
            return await self._finish_error(
                request,
                correlation_id,
                status="DENIED",
                code="TOOL_UNKNOWN",
                message="The requested tool is not registered.",
            )
        if not registration.allowlisted:
            return await self._finish_error(
                request,
                correlation_id,
                status="DENIED",
                code="TOOL_NOT_ALLOWED",
                message="The requested tool is not allowlisted.",
            )
        if registration.lifecycle_state is not ToolLifecycleState.ACTIVE:
            return await self._finish_error(
                request,
                correlation_id,
                status="DENIED",
                code="TOOL_NOT_ACTIVE",
                message="The requested tool is not active.",
            )
        if registration.kill_switch_active:
            return await self._finish_error(
                request,
                correlation_id,
                status="DENIED",
                code="TOOL_KILL_SWITCH_ACTIVE",
                message="The requested tool is disabled by an authoritative kill switch.",
            )
        if self._production and not registration.production_enabled:
            return await self._finish_error(
                request,
                correlation_id,
                status="DENIED",
                code="TEST_TOOL_DISABLED",
                message="Non-production tools are disabled in production.",
            )

        requested_scopes = frozenset(str(item) for item in context.get("scope_refs", []))
        if not registration.required_scopes.issubset(requested_scopes):
            return await self._finish_error(
                request,
                correlation_id,
                status="DENIED",
                code="SCOPE_DENIED",
                message="ToolRequest does not declare all required scopes.",
            )
        if principal is None or context["actor_id"] != principal.actor_id:
            return await self._finish_error(
                request,
                correlation_id,
                status="DENIED",
                code="AUTHORIZATION_DENIED",
                message="Principal context is not authorized.",
            )
        if not requested_scopes.issubset(principal.scopes):
            return await self._finish_error(
                request,
                correlation_id,
                status="DENIED",
                code="SCOPE_DENIED",
                message="Principal scope is not authorized.",
            )
        requested_permissions = frozenset(str(item) for item in context.get("permission_refs", []))
        required_permission = registration.required_permission
        if tool_id in BUSINESS_TOOLS:
            ranks = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "RESTRICTED": 3}
            authority_context = context.get("authority_context", {})
            matching_context = (
                principal is not None
                and context.get("data_scope") == principal.data_scope.value
                and all(
                    context.get(key) == getattr(principal, key)
                    for key in ("division_id", "project_id")
                )
                and set(authority_context.get("role_refs", [])) == set(principal.roles)
                and ranks.get(str(context.get("data_classification")), 99)
                <= ranks[maximum_classification(principal)]
            )
            if not matching_context:
                return await self._finish_error(
                    request,
                    correlation_id,
                    status="DENIED",
                    code="BUSINESS_CONTEXT_DENIED",
                    message="Business scope or classification is not authorized.",
                )
            if principal is None or not business_tool_allowed(tool_id, principal):
                return await self._finish_error(
                    request,
                    correlation_id,
                    status="DENIED",
                    code="BUSINESS_AUTHORITY_DENIED",
                    message="Business authority denied execution.",
                )
            if BUSINESS_TOOLS[tool_id][1] == "overview" and "EXECUTIVE" in principal.roles:
                required_permission = "strategy.read"
        if required_permission not in requested_permissions:
            return await self._finish_error(
                request,
                correlation_id,
                status="DENIED",
                code="PERMISSION_DENIED",
                message="ToolRequest does not declare the required permission.",
            )

        if not self._authorization.is_allowed(
            principal,
            tenant_id=str(context["tenant_id"]),
            organization_id=str(context["organization_id"]),
            workspace_id=str(context["workspace_id"]),
            required_permission=required_permission,
            required_scopes=registration.required_scopes,
        ):
            return await self._finish_error(
                request,
                correlation_id,
                status="DENIED",
                code="AUTHORIZATION_DENIED",
                message="Tenant, organization, workspace, actor, or policy denied execution.",
            )

        arguments = request["arguments"]
        assert isinstance(arguments, Mapping)
        try:
            registration.adapter.validate_arguments(arguments)
        except ToolInputError as exc:
            return await self._finish_error(
                request,
                correlation_id,
                status="REJECTED",
                code="TOOL_INPUT_INVALID",
                message="Tool input does not satisfy the registered input contract.",
                details={"reason": str(exc)},
            )

        idempotency_key = request.get("idempotency_key")
        if registration.idempotency_policy is IdempotencyPolicy.REQUIRED and not isinstance(
            idempotency_key, str
        ):
            return await self._finish_error(
                request,
                correlation_id,
                status="REJECTED",
                code="IDEMPOTENCY_REQUIRED",
                message="The requested tool requires an idempotency key.",
            )

        cache_key: tuple[str, str, str] | None = None
        request_digest: str | None = None
        if isinstance(idempotency_key, str):
            cache_key = (
                str(context["tenant_id"]),
                str(request["tool_id"]),
                idempotency_key,
            )
            request_digest = self._request_digest(request)
            async with self._idempotency_store.serialize(cache_key):
                existing = await self._idempotency_store.get(cache_key)
                if existing is not None:
                    if existing.request_digest != request_digest:
                        return await self._finish_error(
                            request,
                            correlation_id,
                            status="REJECTED",
                            code="IDEMPOTENCY_CONFLICT",
                            message=(
                                "The idempotency key belongs to a different immutable request."
                            ),
                        )
                    replay = self._base_result(request, correlation_id, status="SUCCESS")
                    replay["output"] = existing.output
                    outcome = await self._finish(request, correlation_id, replay)
                    await self._audit(request, correlation_id, "IDEMPOTENT_REPLAY")
                    return outcome

                outcome = await self._execute_adapter(
                    request, correlation_id, registration, arguments
                )
                if outcome.result["status"] == "SUCCESS":
                    await self._idempotency_store.put(
                        cache_key,
                        IdempotencyRecord(
                            request_digest=request_digest,
                            output=outcome.result["output"],
                        ),
                    )
                return outcome

        outcome = await self._execute_adapter(request, correlation_id, registration, arguments)
        return outcome

    async def _execute_adapter(
        self,
        request: Mapping[str, Any],
        correlation_id: str,
        registration: ToolRegistration,
        arguments: Mapping[str, Any],
    ) -> ToolExecutionOutcome:
        try:
            async with asyncio.timeout(registration.timeout_seconds):
                execution_context = request["execution_context"]
                assert isinstance(execution_context, Mapping)
                output = await registration.adapter.execute(
                    arguments,
                    execution_context=execution_context,
                )
        except TimeoutError:
            return await self._finish_error(
                request,
                correlation_id,
                status="TIMEOUT",
                code="TOOL_TIMEOUT",
                message="The tool exceeded its execution timeout.",
                retryable=True,
            )
        except Exception:
            return await self._finish_error(
                request,
                correlation_id,
                status="FAILED",
                code="TOOL_EXECUTION_FAILED",
                message="The tool adapter failed.",
            )

        result = self._base_result(request, correlation_id, status="SUCCESS")
        result["output"] = output
        return await self._finish(request, correlation_id, result)

    async def _finish_error(
        self,
        request: Mapping[str, Any],
        correlation_id: str,
        *,
        status: str,
        code: str,
        message: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> ToolExecutionOutcome:
        result = self._base_result(request, correlation_id, status=status)
        error: dict[str, Any] = {
            "code": code,
            "message": message,
            "correlation_id": correlation_id,
            "retryable": retryable,
        }
        if details is not None:
            error["details"] = details
        result["error"] = error
        return await self._finish(request, correlation_id, result)

    async def _finish(
        self,
        request: Mapping[str, Any],
        correlation_id: str,
        result: dict[str, Any],
    ) -> ToolExecutionOutcome:
        if result["status"] == "SUCCESS" and request["tool_id"] in BUSINESS_TOOLS:
            output = result["output"]
            context = request["execution_context"]
            digest = hashlib.sha256(json.dumps(output, sort_keys=True).encode()).hexdigest()
            source_id = f"source_{request['tool_call_id']}"
            evidence = {
                **{
                    key: context[key]
                    for key in (
                        "tenant_id",
                        "organization_id",
                        "workspace_id",
                        "correlation_id",
                        "scope_refs",
                        "data_classification",
                    )
                },
                "run_id": request["run_id"],
                "source_id": source_id,
                "evidence_id": f"evidence_{request['tool_call_id']}",
                "uri": f"urn:alos:business:{request['tool_id']}:{source_id}",
                "captured_at": output["captured_at"],
                "content_hash": f"sha256:{digest}",
                "source_version": digest,
                "anchor": "data",
                "source_type": "INTERNAL",
                "freshness": "CURRENT",
                "validation_status": "VALID",
                "instruction_authority": False,
            }
            if self._evidence_registry is not None:
                evidence = await resolve_registry_result(self._evidence_registry.register(evidence))
            result["source_refs"], result["evidence_refs"] = [source_id], [evidence]
        self._contract_validator.validate_result(result)
        await self._audit(request, correlation_id, str(result["status"]))
        return ToolExecutionOutcome(correlation_id=correlation_id, result=result)

    @staticmethod
    def _base_result(
        request: Mapping[str, Any], correlation_id: str, *, status: str
    ) -> dict[str, Any]:
        return {
            "tool_call_id": str(request["tool_call_id"]),
            "run_id": str(request["run_id"]),
            "tool_id": str(request["tool_id"]),
            "correlation_id": correlation_id,
            "status": status,
            "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        }

    async def _audit(
        self,
        request: Mapping[str, Any],
        correlation_id: str,
        outcome: str,
    ) -> None:
        context = request["execution_context"]
        assert isinstance(context, Mapping)
        await self._audit_sink.append(
            ToolAuditRecord(
                tool_call_id=str(request["tool_call_id"]),
                run_id=str(request["run_id"]),
                tool_id=str(request["tool_id"]),
                tenant_id=str(context["tenant_id"]),
                organization_id=str(context["organization_id"]),
                workspace_id=str(context["workspace_id"]),
                actor_id=str(context["actor_id"]),
                correlation_id=correlation_id,
                outcome=outcome,
                occurred_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _request_digest(request: Mapping[str, Any]) -> str:
        context = dict(request["execution_context"])
        context.pop("correlation_id", None)
        immutable = {
            "tool_id": request["tool_id"],
            "execution_context": context,
            "arguments": request["arguments"],
        }
        encoded = json.dumps(immutable, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()
