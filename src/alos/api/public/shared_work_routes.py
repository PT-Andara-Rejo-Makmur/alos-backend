"""Dedicated public Projects and Tasks boundary."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Query, Request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from alos.audit import AuditEvent
from alos.authorization import AuthorizationEnforcer
from alos.contracts import CanonicalContractCatalog, ContractValidationError
from alos.dependencies import (
    AuthorizationEnforcerDependency,
    ContractCatalogDependency,
    CurrentPrincipalDependency,
)
from alos.domains.shared_work import SharedWorkService
from alos.identity import Principal
from alos.observability.correlation import current_correlation_id
from alos.security.errors import PlatformError

router = APIRouter(prefix="/api/v1", tags=["shared-work"])
SCHEMA = "https://schemas.alos.dev/v1/shared-work/shared-work.schema.json#/$defs/"


def _service(request: Request) -> SharedWorkService:
    return cast(SharedWorkService, request.app.state.shared_work_service)


def _validate(
    contracts: CanonicalContractCatalog, name: str, payload: dict[str, Any]
) -> dict[str, Any]:
    try:
        return contracts.validate(SCHEMA + name, payload)
    except ContractValidationError as exc:
        raise PlatformError(
            "WORK_CONTRACT_INVALID",
            "Work data does not satisfy its canonical contract.",
            status_code=422 if name.endswith("CreateRequest") else 503,
        ) from exc


async def _authorize(
    authorization: AuthorizationEnforcer,
    principal: Principal,
    *,
    permission: str,
    legacy_permission: str,
    command: str,
) -> str:
    selected = permission if permission in principal.permissions else legacy_permission
    correlation_id = current_correlation_id()
    decision = await authorization.enforce(
        principal=principal,
        required_permission=selected,
        correlation_id=correlation_id,
        command=command,
    )
    if not decision.is_allowed:
        raise PlatformError(
            "AUTHORIZATION_DENIED", f"{permission} permission is required.", status_code=403
        )
    return correlation_id


async def _run[ResultT](awaitable: Awaitable[ResultT]) -> ResultT:
    try:
        return await awaitable
    except IntegrityError as exc:
        raise PlatformError(
            "WORK_CONSTRAINT_CONFLICT", "Work record conflicts with existing data.", status_code=409
        ) from exc
    except SQLAlchemyError as exc:
        raise PlatformError(
            "WORK_STORAGE_FAILURE",
            "Work data is temporarily unavailable.",
            status_code=503,
            retryable=True,
        ) from exc


async def _record_creation(
    request: Request,
    principal: Principal,
    correlation_id: str,
    *,
    entity: str,
    record_id: str,
) -> None:
    await request.app.state.identity_audit.append(
        AuditEvent(
            event_type=f"{entity}.created",
            entity_type=entity,
            entity_id=record_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
            outcome="SUCCEEDED",
            occurred_at=datetime.now(UTC),
            reason=f"Created workspace-visible {entity}",
        )
    )


@router.get("/projects")
async def list_projects(
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
    status: str | None = None,
    search: str | None = Query(default=None, max_length=200),
) -> list[dict[str, Any]]:
    await _authorize(
        authorization,
        principal,
        permission="project.read",
        legacy_permission="work.read",
        command="project.list",
    )
    rows = await _run(_service(request).list_projects(principal, status=status, search=search))
    return [_validate(contracts, "ProjectProjection", row) for row in rows]


@router.post("/projects", status_code=201)
async def create_project(
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        permission="project.create",
        legacy_permission="work.write",
        command="project.create",
    )
    values = _validate(contracts, "ProjectCreateRequest", payload)
    row = await _run(_service(request).create_project(principal, values))
    projection = _validate(contracts, "ProjectProjection", row)
    await _record_creation(
        request, principal, correlation_id, entity="project", record_id=str(row["project_id"])
    )
    return projection


@router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    await _authorize(
        authorization,
        principal,
        permission="project.read",
        legacy_permission="work.read",
        command="project.get",
    )
    row = await _run(_service(request).get_project(principal, project_id))
    return _validate(contracts, "ProjectProjection", row)


@router.get("/tasks")
async def list_tasks(
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
    status: str | None = None,
    priority: str | None = None,
    search: str | None = Query(default=None, max_length=200),
) -> list[dict[str, Any]]:
    await _authorize(
        authorization,
        principal,
        permission="task.read",
        legacy_permission="work.read",
        command="task.list",
    )
    rows = await _run(
        _service(request).list_tasks(principal, status=status, priority=priority, search=search)
    )
    return [_validate(contracts, "TaskProjection", row) for row in rows]


@router.post("/tasks", status_code=201)
async def create_task(
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        permission="task.create",
        legacy_permission="work.write",
        command="task.create",
    )
    values = _validate(contracts, "TaskCreateRequest", payload)
    row = await _run(_service(request).create_task(principal, values))
    projection = _validate(contracts, "TaskProjection", row)
    await _record_creation(
        request, principal, correlation_id, entity="task", record_id=str(row["task_id"])
    )
    return projection


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    await _authorize(
        authorization,
        principal,
        permission="task.read",
        legacy_permission="work.read",
        command="task.get",
    )
    row = await _run(_service(request).get_task(principal, task_id))
    return _validate(contracts, "TaskProjection", row)
