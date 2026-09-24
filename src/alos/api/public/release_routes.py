"""Minimal authenticated release lifecycle command API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter

from alos.dependencies import (
    AgentLifecycleDependency,
    CurrentPrincipalDependency,
    ReleaseAuthorityDependency,
)
from alos.governance.materiality import Materiality
from alos.identity import Principal
from alos.observability.correlation import current_correlation_id
from alos.releases import ReleaseConflictError, ReleaseNotFoundError
from alos.reviews.decisions import AuthoritativeDecision, AuthorityLevel, DecisionOutcome
from alos.security.errors import PlatformError

router = APIRouter(prefix="/api/v1/releases", tags=["releases"])

_ACTION_AUTHORITY = {
    "it-decision": ("release.decide.it", "IT_ADMIN"),
    "director-decision": ("release.decide.director", "EXECUTIVE"),
    "release": ("release.manage", "IT_ADMIN"),
    "activate": ("release.manage", "IT_ADMIN"),
    "suspend": ("release.manage", "IT_ADMIN"),
    "kill": ("release.manage", "IT_ADMIN"),
    "clear-kill": ("release.manage", "IT_ADMIN"),
    "rollback": ("release.manage", "IT_ADMIN"),
}


def _authorize(
    principal: Principal,
    payload: dict[str, Any],
    *,
    permission: str,
    role: str,
) -> None:
    if (
        not principal.active
        or permission not in principal.permissions
        or role not in principal.roles
    ):
        raise PlatformError(
            "RELEASE_NOT_AUTHORIZED",
            "Authenticated principal lacks release lifecycle authority.",
            status_code=403,
        )
    expected = {
        "actor_id": principal.actor_id,
        "tenant_id": principal.tenant_id,
        "organization_id": principal.organization_id,
        "workspace_id": principal.workspace_id,
    }
    for field, value in expected.items():
        if field in payload and str(payload[field]) != value:
            raise PlatformError(
                "RELEASE_AUTHORITY_MISMATCH",
                f"Request {field} conflicts with authenticated principal.",
                status_code=403,
            )


@router.post("")
async def create_release(
    payload: dict[str, Any],
    principal: CurrentPrincipalDependency,
    authority: ReleaseAuthorityDependency,
) -> dict[str, Any]:
    _authorize(principal, payload, permission="release.create", role="AI_ADMIN")
    try:
        release = await authority.create(
            release_id=str(payload["release_id"]),
            review_id=str(payload["review_id"]),
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            subject_id=str(payload["subject_id"]),
            subject_version=str(payload["subject_version"]),
            materiality=Materiality(str(payload.get("materiality", "NON_MATERIAL"))),
            actor_id=principal.actor_id,
            correlation_id=current_correlation_id(),
        )
    except (KeyError, ValueError, ReleaseConflictError) as exc:
        raise PlatformError("RELEASE_COMMAND_REJECTED", str(exc), status_code=409) from exc
    return _response(release)


@router.get("/{release_id}")
async def get_release(
    release_id: str,
    principal: CurrentPrincipalDependency,
    authority: ReleaseAuthorityDependency,
) -> dict[str, Any]:
    try:
        release = await authority.get(
            release_id,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
        )
    except ReleaseNotFoundError as exc:
        raise PlatformError("RELEASE_NOT_FOUND", str(exc), status_code=404) from exc
    return _response(release)


@router.post("/{release_id}/actions/{action}")
async def release_action(
    release_id: str,
    action: str,
    payload: dict[str, Any],
    principal: CurrentPrincipalDependency,
    authority: ReleaseAuthorityDependency,
    lifecycle: AgentLifecycleDependency,
) -> dict[str, Any]:
    required = _ACTION_AUTHORITY.get(action)
    if required is None:
        raise PlatformError("RELEASE_ACTION_UNKNOWN", "Unknown release action.", status_code=404)
    _authorize(principal, payload, permission=required[0], role=required[1])
    try:
        current = await authority.get(
            release_id,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
        )
        correlation_id = current_correlation_id()
        release = await _execute_action(
            authority,
            lifecycle,
            current,
            release_id,
            action,
            payload,
            principal,
            correlation_id,
        )
    except PlatformError:
        raise
    except (KeyError, ValueError, ReleaseConflictError, ReleaseNotFoundError) as exc:
        raise PlatformError("RELEASE_COMMAND_REJECTED", str(exc), status_code=409) from exc
    return _response(release)


async def _execute_action(
    authority: ReleaseAuthorityDependency,
    lifecycle: AgentLifecycleDependency,
    current: Any,
    release_id: str,
    action: str,
    payload: dict[str, Any],
    principal: Principal,
    correlation_id: str,
) -> Any:
    if action in {"it-decision", "director-decision"}:
        level = AuthorityLevel.IT if action == "it-decision" else AuthorityLevel.DIRECTOR
        supplied_level = payload.get("authority_level")
        if supplied_level is not None and str(supplied_level) != level.value:
            raise PlatformError(
                "RELEASE_AUTHORITY_MISMATCH",
                "Request authority_level conflicts with authenticated action authority.",
                status_code=403,
            )
        decision = AuthoritativeDecision(
            decision_id=str(payload["decision_id"]),
            review_id=current.review_id,
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            release_id=release_id,
            correlation_id=correlation_id,
            subject_id=current.subject_id,
            authority=level,
            outcome=DecisionOutcome(str(payload["outcome"])),
            actor_id=principal.actor_id,
            rationale=str(payload["rationale"]),
            decided_at=datetime.now(UTC),
        )
        if level is AuthorityLevel.IT:
            return await authority.record_it_decision(
                release_id, decision, correlation_id=correlation_id
            )
        return await authority.record_director_decision(
            release_id, decision, correlation_id=correlation_id
        )
    if action == "release":
        return await authority.release(
            release_id, actor_id=principal.actor_id, correlation_id=correlation_id
        )
    if action == "activate":
        return await lifecycle.activate(
            release_id, actor_id=principal.actor_id, correlation_id=correlation_id
        )
    if action == "suspend":
        return await lifecycle.suspend(
            release_id,
            actor_id=principal.actor_id,
            reason=str(payload.get("reason", "")),
            correlation_id=correlation_id,
        )
    if action == "kill":
        return await lifecycle.activate_kill_switch(
            release_id,
            actor_id=principal.actor_id,
            reason=str(payload.get("reason", "")),
            correlation_id=correlation_id,
        )
    if action == "clear-kill":
        return await lifecycle.clear_kill_switch(
            release_id,
            actor_id=principal.actor_id,
            reason=str(payload.get("reason", "")),
            correlation_id=correlation_id,
        )
    return await lifecycle.rollback(
        release_id,
        target_release_id=str(payload["target_release_id"]),
        actor_id=principal.actor_id,
        reason=str(payload.get("reason", "")),
        correlation_id=correlation_id,
    )


def _response(release: Any) -> dict[str, Any]:
    return {
        "release_id": release.release_id,
        "review_id": release.review_id,
        "subject_id": release.subject_id,
        "subject_version": release.subject_version,
        "state": release.state.value,
        "correlation_id": release.correlation_id,
        "kill_switch_active": release.kill_switch_active,
        "rollback_target_release_id": release.rollback_target_release_id,
        "ever_released": release.ever_released,
    }
