"""Workspace-scoped CRUD API for migration-owned operational domain records."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from alos.audit import AuditEvent
from alos.authorization import AuthorizationEnforcer
from alos.dependencies import AuthorizationEnforcerDependency, CurrentPrincipalDependency
from alos.domains.crud import DomainCrudService, DomainResource
from alos.identity import Principal
from alos.observability.correlation import current_correlation_id
from alos.security.errors import PlatformError

router = APIRouter(prefix="/api/v1/domains", tags=["domain-data"])
workspace_navigation_router = APIRouter(prefix="/api/v1/workspace-navigation", tags=["navigation"])


class WorkspaceNavigationMutation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visible: bool = True
    display_order: int = Field(default=0, ge=0, le=100_000)


def _service(request: Request) -> DomainCrudService:
    return cast(DomainCrudService, request.app.state.domain_crud_service)


def _required_permission(resource: DomainResource, action: str) -> str:
    if resource.global_admin:
        return "identity.accounts.manage"
    namespace = "work" if resource.domain == "shared" else resource.domain
    return f"{namespace}.{action}"


async def _authorize(
    authorization: AuthorizationEnforcer,
    principal: Principal,
    permission: str,
    command: str,
) -> str:
    correlation_id = current_correlation_id()
    decision = await authorization.enforce(
        principal=principal,
        required_permission=permission,
        correlation_id=correlation_id,
        command=command,
    )
    if not decision.is_allowed:
        raise PlatformError(
            "AUTHORIZATION_DENIED",
            f"{permission} permission is required.",
            status_code=403,
        )
    return correlation_id


def _require_global_admin(principal: Principal) -> None:
    if "IT_ADMIN" not in principal.roles or "identity.accounts.manage" not in principal.permissions:
        raise PlatformError(
            "AUTHORIZATION_DENIED",
            "Global navigation catalog management requires IT administrator authority.",
            status_code=403,
        )


async def _record_change(
    request: Request,
    principal: Principal,
    correlation_id: str,
    resource: DomainResource,
    operation: str,
    record_id: str,
) -> None:
    await request.app.state.identity_audit.append(
        AuditEvent(
            event_type=f"domain.record.{operation}",
            entity_type=resource.table,
            entity_id=record_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
            outcome="SUCCEEDED",
            occurred_at=datetime.now(UTC),
            reason=f"Workspace-scoped {resource.domain} record {operation}",
            metadata={"domain": resource.domain, "table": resource.table},
        )
    )


async def _run_database_command[ResultT](awaitable: Awaitable[ResultT]) -> ResultT:
    try:
        return await awaitable
    except IntegrityError as exc:
        raise PlatformError(
            "DOMAIN_CONSTRAINT_CONFLICT",
            "The record conflicts with an existing record or referenced data.",
            status_code=409,
        ) from exc
    except SQLAlchemyError as exc:
        raise PlatformError(
            "DOMAIN_STORAGE_FAILURE",
            "The domain record could not be persisted safely.",
            status_code=503,
            retryable=True,
        ) from exc


@router.get("/{domain}/{table}")
async def list_domain_records(
    domain: str,
    table: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    resource = _service(request).resource(domain, table)
    if resource.global_admin:
        _require_global_admin(principal)
    await _authorize(
        authorization,
        principal,
        _required_permission(resource, "read"),
        f"domain.{domain}.{table}.list",
    )
    filters = {
        key: value
        for key, value in request.query_params.multi_items()
        if key not in {"limit", "offset"}
    }
    return await _run_database_command(
        _service(request).list_records(
            resource, principal, limit=limit, offset=offset, filters=filters
        )
    )


@router.post("/{domain}/{table}", status_code=201)
async def create_domain_record(
    domain: str,
    table: str,
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> dict[str, Any]:
    resource = _service(request).resource(domain, table)
    if resource.global_admin:
        _require_global_admin(principal)
    correlation_id = await _authorize(
        authorization,
        principal,
        _required_permission(resource, "write"),
        f"domain.{domain}.{table}.create",
    )
    created = await _run_database_command(
        _service(request).create_record(resource, principal, payload)
    )
    await _record_change(
        request, principal, correlation_id, resource, "created", str(created[resource.primary_key])
    )
    return created


@router.get("/{domain}/{table}/{record_id}")
async def get_domain_record(
    domain: str,
    table: str,
    record_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> dict[str, Any]:
    resource = _service(request).resource(domain, table)
    if resource.global_admin:
        _require_global_admin(principal)
    await _authorize(
        authorization,
        principal,
        _required_permission(resource, "read"),
        f"domain.{domain}.{table}.read",
    )
    return await _run_database_command(_service(request).get_record(resource, principal, record_id))


@router.patch("/{domain}/{table}/{record_id}")
@router.put("/{domain}/{table}/{record_id}")
async def update_domain_record(
    domain: str,
    table: str,
    record_id: str,
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> dict[str, Any]:
    resource = _service(request).resource(domain, table)
    if resource.global_admin:
        _require_global_admin(principal)
    correlation_id = await _authorize(
        authorization,
        principal,
        _required_permission(resource, "write"),
        f"domain.{domain}.{table}.update",
    )
    updated = await _run_database_command(
        _service(request).update_record(resource, principal, record_id, payload)
    )
    await _record_change(request, principal, correlation_id, resource, "updated", record_id)
    return updated


@router.delete("/{domain}/{table}/{record_id}", status_code=204)
async def delete_domain_record(
    domain: str,
    table: str,
    record_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> Response:
    resource = _service(request).resource(domain, table)
    if resource.global_admin:
        _require_global_admin(principal)
    correlation_id = await _authorize(
        authorization,
        principal,
        _required_permission(resource, "delete"),
        f"domain.{domain}.{table}.delete",
    )
    await _run_database_command(_service(request).delete_record(resource, principal, record_id))
    await _record_change(request, principal, correlation_id, resource, "deleted", record_id)
    return Response(status_code=204)


@workspace_navigation_router.get("")
async def list_workspace_navigation(
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> list[dict[str, Any]]:
    await _authorize(authorization, principal, "navigation.read", "workspace.navigation.list")
    return await _run_database_command(_service(request).list_workspace_navigation(principal))


@workspace_navigation_router.put("/{navigation_item_id}")
async def update_workspace_navigation(
    navigation_item_id: str,
    payload: WorkspaceNavigationMutation,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> dict[str, Any]:
    correlation_id = await _authorize(
        authorization,
        principal,
        "navigation.manage",
        "workspace.navigation.update",
    )
    result = await _run_database_command(
        _service(request).set_workspace_navigation(
            principal,
            navigation_item_id,
            visible=payload.visible,
            display_order=payload.display_order,
        )
    )
    await request.app.state.identity_audit.append(
        AuditEvent(
            event_type="workspace.navigation.updated",
            entity_type="navigation_item",
            entity_id=navigation_item_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
            outcome="SUCCEEDED",
            occurred_at=datetime.now(UTC),
            reason="Workspace navigation visibility changed",
            metadata={"visible": payload.visible, "display_order": payload.display_order},
        )
    )
    return result


@workspace_navigation_router.delete("/{navigation_item_id}", status_code=204)
async def remove_workspace_navigation(
    navigation_item_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> Response:
    correlation_id = await _authorize(
        authorization,
        principal,
        "navigation.manage",
        "workspace.navigation.remove",
    )
    await _run_database_command(
        _service(request).remove_workspace_navigation(principal, navigation_item_id)
    )
    await request.app.state.identity_audit.append(
        AuditEvent(
            event_type="workspace.navigation.removed",
            entity_type="navigation_item",
            entity_id=navigation_item_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
            outcome="SUCCEEDED",
            occurred_at=datetime.now(UTC),
            reason="Workspace navigation item removed",
        )
    )
    return Response(status_code=204)
