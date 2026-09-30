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


async def _present(
    request: Request, principal: Principal, contracts: CanonicalContractCatalog,
    entity_type: str, row: dict[str, Any],
) -> dict[str, Any]:
    presented = await _service(request).present(principal, entity_type, [row])
    return _validate(contracts, f"{entity_type.title()}Projection", presented[0])


async def _present_many(
    request: Request, principal: Principal, contracts: CanonicalContractCatalog,
    entity_type: str, rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    presented = await _service(request).present(principal, entity_type, rows)
    return [
        _validate(contracts, f"{entity_type.title()}Projection", row) for row in presented
    ]


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


@router.get("/workspace-members")
async def list_shared_work_workspace_members(
    request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> list[dict[str, Any]]:
    directory_permissions = (
        "project.read", "task.read", "approval.read", "document.read",
        "report.read", "finding.read", "task.assign", "finding.assign", "work.read",
    )
    selected = next(
        (permission for permission in directory_permissions if permission in principal.permissions),
        "project.read",
    )
    await _authorize(
        authorization, principal, permission=selected, legacy_permission=None,
        command="workspace.members.read",
    )
    rows = await _run(_service(request).list_workspace_members(principal))
    return [_validate(contracts, "WorkspaceMemberProjection", row) for row in rows]


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
    return await _present_many(request, principal, contracts, "DOCUMENT", rows)


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
    projection = await _present(request, principal, contracts, "DOCUMENT", row)
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
    return await _present(request, principal, contracts, "DOCUMENT", row)


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


@router.get("/documents/{document_id}/source-options")
async def list_document_source_options(
    document_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> list[dict[str, Any]]:
    await _authorize(
        authorization, principal, permission="document.version",
        legacy_permission=None, command="document.source.options",
    )
    rows = await _run(_service(request).list_document_source_options(principal, document_id))
    return [_validate(contracts, "DocumentSourceOptionProjection", row) for row in rows]


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
    projection = await _present(request, principal, contracts, "DOCUMENT", row)
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
    projection = await _present(request, principal, contracts, "DOCUMENT", row)
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
    projection = await _present(request, principal, contracts, "DOCUMENT", row)
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
    return await _present_many(request, principal, contracts, "APPROVAL", rows)


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
    projection = await _present(request, principal, contracts, "APPROVAL", row)
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
    return await _present(request, principal, contracts, "APPROVAL", row)


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
    projection = await _present(request, principal, contracts, "APPROVAL", row)
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
    return await _present_many(request, principal, contracts, "PROJECT", rows)


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
    projection = await _present(request, principal, contracts, "PROJECT", row)
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
    return await _present(request, principal, contracts, "PROJECT", row)


@router.get("/projects/{project_id}/relations")
async def list_project_relations(
    project_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> list[dict[str, Any]]:
    await _authorize(
        authorization, principal, permission="project.read", legacy_permission="work.read",
        command="project.relations.read",
    )
    rows = await _run(_service(request).list_project_relations(principal, project_id))
    return [_validate(contracts, "RelationProjection", row) for row in rows]


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
    projection = await _present(request, principal, contracts, "PROJECT", row)
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
    projection = await _present(request, principal, contracts, "PROJECT", row)
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
    return await _present_many(request, principal, contracts, "TASK", rows)


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
    projection = await _present(request, principal, contracts, "TASK", row)
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
    return await _present(request, principal, contracts, "TASK", row)


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
    projection = await _present(request, principal, contracts, "TASK", row)
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
    projection = await _present(request, principal, contracts, "TASK", row)
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
    projection = await _present(request, principal, contracts, "TASK", row)
    if changed:
        await _record_mutation(
            request, principal, correlation_id, entity="task", record_id=task_id, action="completed"
        )
    return projection


@router.get("/work/reports/definitions")
async def list_report_definitions(
    request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> list[dict[str, Any]]:
    await _authorize(
        authorization, principal, permission="report.read", legacy_permission="work.read",
        command="report.definition.list",
    )
    rows = await _run(_service(request).list_report_definitions(principal))
    return [_validate(contracts, "ReportDefinitionProjection", row) for row in rows]


@router.post("/work/reports/definitions", status_code=201)
async def create_report_definition(
    payload: dict[str, Any], request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization, principal, permission="report.create", legacy_permission=None,
        command="report.definition.create",
    )
    values = _validate(contracts, "ReportDefinitionCreateRequest", payload)
    row = await _run(_service(request).create_report_definition(principal, values))
    projection = _validate(contracts, "ReportDefinitionProjection", row)
    await _record_mutation(
        request, principal, correlation_id, entity="report_definition",
        record_id=str(row["report_definition_id"]), action="created",
    )
    return projection


@router.get("/work/reports/definitions/{definition_id}")
async def get_report_definition(
    definition_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    await _authorize(
        authorization, principal, permission="report.read", legacy_permission="work.read",
        command="report.definition.get",
    )
    row = await _run(_service(request).get_report_definition(principal, definition_id))
    return _validate(contracts, "ReportDefinitionProjection", row)


@router.patch("/work/reports/definitions/{definition_id}")
async def update_report_definition(
    definition_id: str, payload: dict[str, Any], request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization, principal, permission="report.create", legacy_permission=None,
        command="report.definition.update",
    )
    values = _validate(contracts, "ReportDefinitionUpdateRequest", payload)
    row = await _run(_service(request).update_report_definition(principal, definition_id, values))
    projection = _validate(contracts, "ReportDefinitionProjection", row)
    await _record_mutation(
        request, principal, correlation_id, entity="report_definition",
        record_id=definition_id, action="updated",
    )
    return projection


@router.get("/work/reports/results")
async def list_report_results(
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
    status: str | None = Query(default=None),
    report_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    await _authorize(
        authorization,
        principal,
        permission="report.read",
        legacy_permission="work.read",
        command="report.read",
    )
    status = _filter(contracts, "ReportStatus", status)
    rows = await _service(request).list_reports(
        principal, status=status, report_type=report_type, search=search
    )
    return await _present_many(request, principal, contracts, "REPORT", rows)


@router.post("/work/reports/results", status_code=201)
async def create_report_result(
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        permission="report.create",
        legacy_permission="work.write",
        command="report.create",
    )
    payload = _validate(contracts, "ReportCreateRequest", await request.json())
    row = await _run(_service(request).create_report(principal, payload))
    projection = await _present(request, principal, contracts, "REPORT", row)
    await _record_mutation(
        request,
        principal,
        correlation_id,
        entity="report",
        record_id=projection["report_id"],
        action="created",
    )
    return projection


@router.get("/work/reports/results/{report_id}")
async def get_report_result(
    report_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    await _authorize(
        authorization,
        principal,
        permission="report.read",
        legacy_permission="work.read",
        command="report.read",
    )
    row = await _service(request).get_report(principal, report_id)
    return await _present(request, principal, contracts, "REPORT", row)


async def _report_transition(
    report_id: str,
    action: str,
    request: Request,
    principal: Principal,
    authorization: AuthorizationEnforcer,
    contracts: CanonicalContractCatalog,
) -> dict[str, Any]:
    permission, audit_action = {
        "submit_review": ("report.create", "review_requested"),
        "review": ("report.review", "approved"),
        "publish": ("report.publish", "published"),
        "archive": ("report.archive", "archived"),
    }[action]
    correlation_id = await _authorize(
        authorization, principal, permission=permission,
        legacy_permission=None, command=f"report.{action}",
    )
    row, changed = await _run(_service(request).transition_report(principal, report_id, action))
    projection = await _present(request, principal, contracts, "REPORT", row)
    if changed:
        await _record_mutation(
            request, principal, correlation_id, entity="report", record_id=report_id,
            action=audit_action,
        )
    return projection


@router.post("/work/reports/results/{report_id}/submit-review")
async def submit_report_review(
    report_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    return await _report_transition(
        report_id, "submit_review", request, principal, authorization, contracts
    )


@router.post("/work/reports/results/{report_id}/review")
async def review_report(
    report_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    return await _report_transition(
        report_id, "review", request, principal, authorization, contracts
    )


@router.post("/work/reports/results/{report_id}/publish")
async def publish_report(
    report_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    return await _report_transition(
        report_id, "publish", request, principal, authorization, contracts
    )


@router.post("/work/reports/results/{report_id}/archive")
async def archive_report(
    report_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    return await _report_transition(
        report_id, "archive", request, principal, authorization, contracts
    )


@router.get("/work/findings")
async def list_findings(
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    source_type: str | None = Query(default=None),
    search: str | None = Query(default=None),
) -> list[dict[str, Any]]:
    await _authorize(
        authorization,
        principal,
        permission="finding.read",
        legacy_permission="work.read",
        command="finding.read",
    )
    status = _filter(contracts, "FindingStatus", status)
    severity = _filter(contracts, "FindingSeverity", severity)
    rows = await _service(request).list_findings(
        principal,
        status=status,
        severity=severity,
        source_type=source_type,
        search=search,
    )
    return await _present_many(request, principal, contracts, "FINDING", rows)


@router.post("/work/findings", status_code=201)
async def create_finding(
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        permission="finding.create",
        legacy_permission="work.write",
        command="finding.create",
    )
    payload = _validate(contracts, "FindingCreateRequest", await request.json())
    row = await _run(_service(request).create_finding(principal, payload))
    projection = await _present(request, principal, contracts, "FINDING", row)
    await _record_mutation(
        request,
        principal,
        correlation_id,
        entity="finding",
        record_id=projection["finding_id"],
        action="created",
    )
    return projection


@router.get("/work/findings/{finding_id}")
async def get_finding(
    finding_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    await _authorize(
        authorization,
        principal,
        permission="finding.read",
        legacy_permission="work.read",
        command="finding.read",
    )
    row = await _service(request).get_finding(principal, finding_id)
    return await _present(request, principal, contracts, "FINDING", row)


@router.patch("/work/findings/{finding_id}")
async def update_finding(
    finding_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization, principal, permission="finding.update",
        legacy_permission=None, command="finding.update",
    )
    values = _validate(contracts, "FindingUpdateRequest", payload)
    row = await _run(_service(request).update_finding(principal, finding_id, values))
    projection = await _present(request, principal, contracts, "FINDING", row)
    await _record_mutation(
        request, principal, correlation_id, entity="finding", record_id=finding_id,
        action="updated",
    )
    return projection


@router.post("/work/findings/{finding_id}/assign")
async def assign_finding(
    finding_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization, principal, permission="finding.assign",
        legacy_permission=None, command="finding.assign",
    )
    values = _validate(contracts, "FindingAssignmentRequest", payload)
    row, changed = await _run(
        _service(request).assign_finding(principal, finding_id, str(values["owner_actor_id"]))
    )
    projection = await _present(request, principal, contracts, "FINDING", row)
    if changed:
        await _record_mutation(
            request, principal, correlation_id, entity="finding", record_id=finding_id,
            action="assigned",
        )
    return projection


async def _finding_transition(
    finding_id: str,
    action: str,
    request: Request,
    principal: Principal,
    authorization: AuthorizationEnforcer,
    contracts: CanonicalContractCatalog,
) -> dict[str, Any]:
    permission, audit_action = {
        "start": ("finding.update", "started"),
        "submit_verification": ("finding.update", "verification_requested"),
        "verify": ("finding.verify", "verified"),
        "close": ("finding.close", "closed"),
    }[action]
    correlation_id = await _authorize(
        authorization, principal, permission=permission,
        legacy_permission=None, command=f"finding.{action}",
    )
    row, changed = await _run(_service(request).transition_finding(principal, finding_id, action))
    projection = await _present(request, principal, contracts, "FINDING", row)
    if changed:
        await _record_mutation(
            request, principal, correlation_id, entity="finding", record_id=finding_id,
            action=audit_action,
        )
    return projection


@router.post("/work/findings/{finding_id}/start")
async def start_finding(
    finding_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    return await _finding_transition(
        finding_id, "start", request, principal, authorization, contracts
    )


@router.post("/work/findings/{finding_id}/submit-verification")
async def submit_finding_verification(
    finding_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    return await _finding_transition(
        finding_id, "submit_verification", request, principal, authorization, contracts
    )


@router.post("/work/findings/{finding_id}/verify")
async def verify_finding(
    finding_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    return await _finding_transition(
        finding_id, "verify", request, principal, authorization, contracts
    )


@router.post("/work/findings/{finding_id}/close")
async def close_finding(
    finding_id: str, request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    return await _finding_transition(
        finding_id, "close", request, principal, authorization, contracts
    )


async def _shared_entity_read(
    entity_type: str, principal: Principal, authorization: AuthorizationEnforcer,
    contracts: CanonicalContractCatalog,
) -> None:
    _filter(contracts, "EntityType", entity_type)
    permission = f"{entity_type.lower()}.read"
    await _authorize(
        authorization, principal, permission=permission, legacy_permission="work.read",
        command=f"work.{entity_type.lower()}.relation.read",
    )


@router.get("/work/{entity_type}/{entity_id}/evidence")
async def list_shared_work_evidence(
    entity_type: str, entity_id: str, request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> list[dict[str, Any]]:
    await _shared_entity_read(entity_type, principal, authorization, contracts)
    rows = await _run(_service(request).list_evidence(principal, entity_type, entity_id))
    return [_validate(contracts, "EvidenceProjection", row) for row in rows]


@router.get("/work/{entity_type}/{entity_id}/relations")
async def list_shared_work_relations(
    entity_type: str, entity_id: str, request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> list[dict[str, Any]]:
    await _shared_entity_read(entity_type, principal, authorization, contracts)
    rows = await _run(_service(request).list_relations(principal, entity_type, entity_id))
    return [_validate(contracts, "RelationProjection", row) for row in rows]


@router.post("/documents/{document_id}/links", status_code=201)
async def link_document_to_work(
    document_id: str, payload: dict[str, Any], request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization, principal, permission="work.relation.link", legacy_permission=None,
        command="work.relation.link",
    )
    values = _validate(contracts, "DocumentLinkRequest", payload)
    await _shared_entity_read(values["target_type"], principal, authorization, contracts)
    row, changed = await _run(_service(request).link_document(
        principal, document_id, values["target_type"], values["target_id"]
    ))
    if changed:
        await _record_mutation(
            request, principal, correlation_id, entity="document",
            record_id=document_id, action="linked",
        )
    return _validate(contracts, "RelationProjection", row)


@router.get("/work/{entity_type}/{entity_id}/checklist")
async def list_shared_work_checklist(
    entity_type: str, entity_id: str, request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> list[dict[str, Any]]:
    _filter(contracts, "ChecklistEntityType", entity_type)
    await _shared_entity_read(entity_type, principal, authorization, contracts)
    rows = await _run(_service(request).list_checklist(principal, entity_type, entity_id))
    return [_validate(contracts, "ChecklistItemProjection", row) for row in rows]


@router.post("/work/{entity_type}/{entity_id}/checklist", status_code=201)
async def create_shared_work_checklist_item(
    entity_type: str, entity_id: str, payload: dict[str, Any], request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    _filter(contracts, "ChecklistEntityType", entity_type)
    correlation_id = await _authorize(
        authorization, principal, permission="work.checklist.manage", legacy_permission=None,
        command="work.checklist.create",
    )
    values = _validate(contracts, "ChecklistCreateRequest", payload)
    if not values["body"].strip():
        raise PlatformError("WORK_CHECKLIST_EMPTY", "Checklist body is required.", status_code=422)
    row = await _run(_service(request).create_checklist_item(
        principal, entity_type, entity_id, values["body"]
    ))
    await _record_mutation(
        request, principal, correlation_id, entity=entity_type.lower(),
        record_id=entity_id, action="checklist_item_created",
    )
    return _validate(contracts, "ChecklistItemProjection", row)


@router.post("/work/{entity_type}/{entity_id}/checklist/{item_id}/complete")
async def complete_shared_work_checklist_item(
    entity_type: str, entity_id: str, item_id: str, request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    _filter(contracts, "ChecklistEntityType", entity_type)
    correlation_id = await _authorize(
        authorization, principal, permission="work.checklist.manage", legacy_permission=None,
        command="work.checklist.complete",
    )
    row, changed = await _run(_service(request).complete_checklist_item(
        principal, entity_type, entity_id, item_id
    ))
    if changed:
        await _record_mutation(
            request, principal, correlation_id, entity=entity_type.lower(),
            record_id=entity_id, action="checklist_item_completed",
        )
    return _validate(contracts, "ChecklistItemProjection", row)


@router.get("/work/evidence-candidates")
async def list_shared_work_evidence_candidates(
    request: Request, principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency, contracts: ContractCatalogDependency,
) -> list[dict[str, Any]]:
    await _authorize(
        authorization, principal, permission="work.evidence.link", legacy_permission=None,
        command="work.evidence.candidates",
    )
    rows = await _run(_service(request).list_evidence_candidates(principal))
    return [_validate(contracts, "EvidenceCandidateProjection", row) for row in rows]


@router.post("/work/{entity_type}/{entity_id}/evidence", status_code=201)
async def link_shared_work_evidence(
    entity_type: str, entity_id: str, payload: dict[str, Any], request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    _filter(contracts, "EntityType", entity_type)
    correlation_id = await _authorize(
        authorization, principal, permission="work.evidence.link", legacy_permission=None,
        command="work.evidence.link",
    )
    values = _validate(contracts, "EvidenceLinkRequest", payload)
    row, changed = await _run(_service(request).link_evidence(
        principal, entity_type, entity_id, values["evidence_id"]
    ))
    projection = _validate(contracts, "EvidenceProjection", row)
    if changed:
        await _record_mutation(
            request, principal, correlation_id, entity=entity_type.lower(),
            record_id=entity_id, action="evidence_linked",
        )
    return projection


@router.get("/work/{entity_type}/{entity_id}/activity")
async def list_shared_work_activity(
    entity_type: str, entity_id: str, request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> list[dict[str, Any]]:
    await _shared_entity_read(entity_type, principal, authorization, contracts)
    rows = await _run(_service(request).list_activity(principal, entity_type, entity_id))
    return [_validate(contracts, "ActivityProjection", row) for row in rows]


@router.get("/work/{entity_type}/{entity_id}/comments")
async def list_shared_work_comments(
    entity_type: str, entity_id: str, request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> list[dict[str, Any]]:
    await _shared_entity_read(entity_type, principal, authorization, contracts)
    rows = await _run(_service(request).list_comments(principal, entity_type, entity_id))
    return [_validate(contracts, "CommentProjection", row) for row in rows]


@router.post("/work/{entity_type}/{entity_id}/comments", status_code=201)
async def create_shared_work_comment(
    entity_type: str, entity_id: str, payload: dict[str, Any], request: Request,
    principal: CurrentPrincipalDependency, authorization: AuthorizationEnforcerDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    _filter(contracts, "EntityType", entity_type)
    correlation_id = await _authorize(
        authorization, principal, permission="work.comment.create", legacy_permission=None,
        command="work.comment.create",
    )
    values = _validate(contracts, "CommentRequest", payload)
    if not values["body"].strip():
        raise PlatformError(
            "WORK_COMMENT_EMPTY", "Comment body is required.", status_code=422
        )
    row = await _run(_service(request).create_comment(
        principal, entity_type, entity_id, values["body"]
    ))
    projection = _validate(contracts, "CommentProjection", row)
    await _record_mutation(
        request, principal, correlation_id, entity=entity_type.lower(),
        record_id=entity_id, action="commented",
    )
    return projection

