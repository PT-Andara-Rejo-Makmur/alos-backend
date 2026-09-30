"""Dedicated public Shared Work boundary."""

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
SCHEMA_DOCUMENT = SCHEMA.split("#", maxsplit=1)[0]
CLASSIFICATION_SCHEMA = "https://schemas.alos.dev/v1/common/data-classification.schema.json"


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
            status_code=422 if name.endswith("Request") else 503,
        ) from exc


def _filter(contracts: CanonicalContractCatalog, name: str, value: str | None) -> str | None:
    if value is not None and value not in contracts.enum_values(SCHEMA_DOCUMENT, name):
        raise PlatformError(
            "WORK_FILTER_INVALID",
            "Filter value is outside the canonical vocabulary.",
            status_code=422,
        )
    return value


async def _authorize(
    authorization: AuthorizationEnforcer,
    principal: Principal,
    *,
    permission: str,
    legacy_permission: str | None,
    command: str,
) -> str:
    selected = (
        permission
        if permission in principal.permissions or legacy_permission is None
        else legacy_permission
    )
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


async def _record_mutation(
    request: Request,
    principal: Principal,
    correlation_id: str,
    *,
    entity: str,
    record_id: str,
    action: str,
) -> None:
    await request.app.state.identity_audit.append(
        AuditEvent(
            event_type=f"{entity}.{action}",
            entity_type=entity,
            entity_id=record_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
            outcome="SUCCEEDED",
            occurred_at=datetime.now(UTC),
            reason=f"{action.capitalize()} workspace-visible {entity}",
        )
    )


@router.get("/documents")
async def list_documents(
    request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
    status: str | None = Query(default=None),
    classification: str | None = Query(default=None),
    category: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    await _authorize(
        authorization, principal, permission="document.read",
        legacy_permission="work.read", command="document.list",
    )
    if classification is not None and classification not in contracts.enum_values(
        CLASSIFICATION_SCHEMA, ""
    ):
        raise PlatformError(
            "WORK_FILTER_INVALID", "Filter value is outside the canonical vocabulary.",
            status_code=422,
        )
    rows = await _run(_service(request).list_documents(
        principal, status=_filter(contracts, "DocumentStatus", status),
        classification=classification, category=category, search=search,
    ))
    return [_validate(contracts, "DocumentProjection", row) for row in rows]


@router.post("/documents", status_code=201)
async def create_document(
    payload: dict[str, Any], request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization, principal, permission="document.create",
        legacy_permission="work.write", command="document.create",
    )
    values = _validate(contracts, "DocumentCreateRequest", payload)
    row = await _run(_service(request).create_document(principal, values))
    projection = _validate(contracts, "DocumentProjection", row)
    await _record_mutation(
        request, principal, correlation_id, entity="document",
        record_id=str(row["document_id"]), action="created",
    )
    return projection


@router.get("/documents/{document_id}")
async def get_document(
    document_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    await _authorize(
        authorization, principal, permission="document.read",
        legacy_permission="work.read", command="document.get",
    )
    row = await _run(_service(request).get_document(principal, document_id))
    return _validate(contracts, "DocumentProjection", row)


@router.get("/documents/{document_id}/versions")
async def list_document_versions(
    document_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> list[dict[str, Any]]:
    await _authorize(
        authorization, principal, permission="document.read",
        legacy_permission="work.read", command="document.version.list",
    )
    rows = await _run(_service(request).list_document_versions(principal, document_id))
    return [_validate(contracts, "DocumentVersionProjection", row) for row in rows]


@router.post("/documents/{document_id}/versions", status_code=201)
async def create_document_version(
    document_id: str, payload: dict[str, Any], request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization, principal, permission="document.version",
        legacy_permission=None, command="document.version.create",
    )
    values = _validate(contracts, "DocumentVersionCreateRequest", payload)
    row = await _run(_service(request).create_document_version(principal, document_id, values))
    projection = _validate(contracts, "DocumentVersionProjection", row)
    await _record_mutation(
        request, principal, correlation_id, entity="document",
        record_id=document_id, action="versioned",
    )
    return projection


@router.post("/documents/{document_id}/review")
async def review_document(
    document_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        permission="document.review",
        legacy_permission=None,
        command="document.review",
    )
    row, changed = await _run(_service(request).review_document(principal, document_id))
    projection = _validate(contracts, "DocumentProjection", row)
    if changed:
        await _record_mutation(
            request,
            principal,
            correlation_id,
            entity="document",
            record_id=document_id,
            action="reviewed",
        )
    return projection


@router.post("/documents/{document_id}/approve")
async def approve_document(
    document_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        permission="document.approve",
        legacy_permission=None,
        command="document.approve",
    )
    row, changed = await _run(_service(request).approve_document(principal, document_id))
    projection = _validate(contracts, "DocumentProjection", row)
    if changed:
        await _record_mutation(
            request,
            principal,
            correlation_id,
            entity="document",
            record_id=document_id,
            action="approved",
        )
    return projection


@router.post("/documents/{document_id}/retire")
async def retire_document(
    document_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        permission="document.retire",
        legacy_permission=None,
        command="document.retire",
    )
    row, changed = await _run(_service(request).retire_document(principal, document_id))
    projection = _validate(contracts, "DocumentProjection", row)
    if changed:
        await _record_mutation(
            request,
            principal,
            correlation_id,
            entity="document",
            record_id=document_id,
            action="retired",
        )
    return projection


@router.get("/approvals")
async def list_approvals(
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
    status: str | None = Query(default=None),
    subject_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    await _authorize(
        authorization, principal, permission="approval.read",
        legacy_permission="work.read", command="approval.list",
    )
    rows = await _run(_service(request).list_approvals(
        principal,
        status=_filter(contracts, "ApprovalStatus", status),
        subject_type=_filter(contracts, "ApprovalSubjectType", subject_type),
        search=search,
    ))
    return [_validate(contracts, "ApprovalProjection", row) for row in rows]


@router.post("/approvals", status_code=201)
async def request_approval(
    payload: dict[str, Any], request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization, principal, permission="approval.request",
        legacy_permission="work.write", command="approval.request",
    )
    values = _validate(contracts, "ApprovalRequest", payload)
    row = await _run(_service(request).request_approval(principal, values))
    projection = _validate(contracts, "ApprovalProjection", row)
    await _record_mutation(
        request, principal, correlation_id, entity="approval",
        record_id=str(row["approval_id"]), action="requested",
    )
    return projection


@router.get("/approvals/{approval_id}")
async def get_approval(
    approval_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    await _authorize(
        authorization, principal, permission="approval.read",
        legacy_permission="work.read", command="approval.get",
    )
    row = await _run(_service(request).get_approval(principal, approval_id))
    return _validate(contracts, "ApprovalProjection", row)


async def _decide_approval(
    action: str, approval_id: str, payload: dict[str, Any], request: Request,
    principal: Principal, authorization: AuthorizationEnforcer,
    contracts: CanonicalContractCatalog,
) -> dict[str, Any]:
    transitions = {
        "approve": ("APPROVED", "APPROVED"),
        "return": ("RETURNED", "RETURNED"),
        "reject": ("REJECTED", "REJECTED"),
        "hold": ("HELD", "HOLD"),
    }
    status, decision = transitions[action]
    correlation_id = await _authorize(
        authorization, principal, permission=f"approval.{action}",
        legacy_permission=None, command=f"approval.{action}",
    )
    values = _validate(contracts, "ApprovalDecisionRequest", payload)
    row, changed = await _run(_service(request).decide_approval(
        principal, approval_id, status=status, decision=decision,
        decision_reason=values.get("decision_reason"),
    ))
    projection = _validate(contracts, "ApprovalProjection", row)
    if changed:
        await _record_mutation(
            request, principal, correlation_id, entity="approval",
            record_id=approval_id, action=action,
        )
    return projection


@router.post("/approvals/{approval_id}/approve")
async def approve_approval(
    approval_id: str, payload: dict[str, Any], request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    return await _decide_approval(
        "approve", approval_id, payload, request, principal, authorization, contracts
    )


@router.post("/approvals/{approval_id}/return")
async def return_approval(
    approval_id: str, payload: dict[str, Any], request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    return await _decide_approval(
        "return", approval_id, payload, request, principal, authorization, contracts
    )


@router.post("/approvals/{approval_id}/reject")
async def reject_approval(
    approval_id: str, payload: dict[str, Any], request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    return await _decide_approval(
        "reject", approval_id, payload, request, principal, authorization, contracts
    )


@router.post("/approvals/{approval_id}/hold")
async def hold_approval(
    approval_id: str, payload: dict[str, Any], request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    return await _decide_approval(
        "hold", approval_id, payload, request, principal, authorization, contracts
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
    rows = await _run(
        _service(request).list_projects(
            principal, status=_filter(contracts, "ProjectStatus", status), search=search
        )
    )
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
    await _record_mutation(
        request,
        principal,
        correlation_id,
        entity="project",
        record_id=str(row["project_id"]),
        action="created",
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


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        permission="project.update",
        legacy_permission="work.write",
        command="project.update",
    )
    values = _validate(contracts, "ProjectUpdateRequest", payload)
    row = await _run(_service(request).update_project(principal, project_id, values))
    projection = _validate(contracts, "ProjectProjection", row)
    await _record_mutation(
        request, principal, correlation_id, entity="project", record_id=project_id, action="updated"
    )
    return projection


@router.post("/projects/{project_id}/archive")
async def archive_project(
    project_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        permission="project.archive",
        legacy_permission=None,
        command="project.archive",
    )
    row, changed = await _run(_service(request).archive_project(principal, project_id))
    projection = _validate(contracts, "ProjectProjection", row)
    if changed:
        await _record_mutation(
            request,
            principal,
            correlation_id,
            entity="project",
            record_id=project_id,
            action="archived",
        )
    return projection


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
        _service(request).list_tasks(
            principal,
            status=_filter(contracts, "TaskStatus", status),
            priority=_filter(contracts, "TaskPriority", priority),
            search=search,
        )
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
    await _record_mutation(
        request,
        principal,
        correlation_id,
        entity="task",
        record_id=str(row["task_id"]),
        action="created",
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


@router.patch("/tasks/{task_id}")
async def update_task(
    task_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        permission="task.update",
        legacy_permission="work.write",
        command="task.update",
    )
    values = _validate(contracts, "TaskUpdateRequest", payload)
    row = await _run(_service(request).update_task(principal, task_id, values))
    projection = _validate(contracts, "TaskProjection", row)
    await _record_mutation(
        request, principal, correlation_id, entity="task", record_id=task_id, action="updated"
    )
    return projection


@router.post("/tasks/{task_id}/assign")
async def assign_task(
    task_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        permission="task.assign",
        legacy_permission=None,
        command="task.assign",
    )
    values = _validate(contracts, "TaskAssignRequest", payload)
    row = await _run(
        _service(request).assign_task(principal, task_id, str(values["owner_actor_id"]))
    )
    projection = _validate(contracts, "TaskProjection", row)
    await _record_mutation(
        request, principal, correlation_id, entity="task", record_id=task_id, action="assigned"
    )
    return projection


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        permission="task.complete",
        legacy_permission=None,
        command="task.complete",
    )
    row, changed = await _run(_service(request).complete_task(principal, task_id))
    projection = _validate(contracts, "TaskProjection", row)
    if changed:
        await _record_mutation(
            request, principal, correlation_id, entity="task", record_id=task_id, action="completed"
        )
    return projection
