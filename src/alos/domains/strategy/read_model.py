"""Backend-owned read selection and action projection from authoritative Strategy records."""

from datetime import datetime
from typing import Any

from alos.domains.strategy.measurement import performance_state, selected_observations
from alos.domains.strategy.models import LifecycleState, Objective, ObservationKind, Plan, Target
from alos.domains.strategy.projections import project, project_domains
from alos.domains.strategy.service import StrategyService
from alos.identity import Principal


def plan_projection(plan: Plan, principal: Principal) -> dict[str, Any]:
    data = project(plan)
    if plan.plan_type.value == "STRATEGIC_PLAN":
        data.pop("strategic_plan_id", None)
        data.pop("strategic_plan_version", None)
    actions: list[str] = []
    own = plan.owner_workspace_id == principal.workspace_id
    manage = own and (
        (
            plan.scope.type == "COMPANY"
            and "EXECUTIVE" in principal.roles
            and "strategy.company.manage" in principal.permissions
        )
        or (
            plan.scope.type != "COMPANY"
            and "DIVISION_LEAD" in principal.roles
            and "strategy.division.manage" in principal.permissions
        )
    )
    if plan.lifecycle_state is LifecycleState.DRAFT and manage:
        actions.extend(("EDIT", "SUBMIT"))
    if own and "EXECUTIVE" in principal.roles:
        if (
            plan.lifecycle_state is LifecycleState.UNDER_REVIEW
            and "strategy.approve" in principal.permissions
        ):
            actions.append("APPROVE")
        if (
            plan.lifecycle_state is LifecycleState.APPROVED
            and "strategy.activate" in principal.permissions
        ):
            actions.append("ACTIVATE")
        if (
            plan.lifecycle_state is LifecycleState.ACTIVE
            and "strategy.activate" in principal.permissions
        ):
            actions.append("ARCHIVE")
    data["authorized_actions"] = project(actions)
    return data


async def target_detail(
    service: StrategyService, principal: Principal, target: Target
) -> dict[str, Any]:
    observations = await service.repository.list_observations(target.target_id, target.version)
    observations = tuple(
        sorted(
            observations,
            key=lambda item: (
                item.observed_at,
                item.recorded_at or item.observed_at,
                item.observation_id,
            ),
            reverse=True,
        )
    )
    selected = selected_observations(observations)
    plan = await service.get_plan(principal, target.plan_id, target.plan_version)
    relationships = await service.repository.list_relationships(
        target.tenant_id, target.organization_id
    )
    revisions = await service.repository.list_revisions(target.target_id)
    own = target.owner_workspace_id == principal.workspace_id
    manage = own and (
        (
            target.scope.type == "COMPANY"
            and "EXECUTIVE" in principal.roles
            and "strategy.company.manage" in principal.permissions
        )
        or (
            target.scope.type != "COMPANY"
            and "DIVISION_LEAD" in principal.roles
            and "strategy.division.manage" in principal.permissions
        )
    )
    revision = any(item.to_version == target.version for item in revisions)
    actions: list[str] = []
    if (
        manage
        and target.lifecycle_state is LifecycleState.DRAFT
        and (
            plan.lifecycle_state is LifecycleState.DRAFT
            or (revision and plan.lifecycle_state is LifecycleState.ACTIVE)
        )
    ):
        actions.extend(("EDIT", "RECORD_TARGET", "RECORD_FORECAST"))
        if revision:
            actions.append("SUBMIT")
    if manage and target.lifecycle_state is LifecycleState.ACTIVE:
        actions.extend(("RECORD_ACTUAL", "RECORD_FORECAST"))
        latest = await service.repository.get_target(target.target_id)
        if latest is not None and latest.version == target.version:
            actions.append("REVISE")
    if (
        own
        and "EXECUTIVE" in principal.roles
        and target.lifecycle_state not in {LifecycleState.SUPERSEDED, LifecycleState.ARCHIVED}
    ):
        if "strategy.review" in principal.permissions:
            actions.append("VERIFY_PLANNING")
        if revision and plan.lifecycle_state is LifecycleState.ACTIVE:
            if (
                target.lifecycle_state is LifecycleState.UNDER_REVIEW
                and "strategy.approve" in principal.permissions
            ):
                actions.append("APPROVE")
            if (
                target.lifecycle_state is LifecycleState.APPROVED
                and "strategy.activate" in principal.permissions
            ):
                actions.append("ACTIVATE")
    if (
        own
        and principal.roles.intersection({"EXECUTIVE", "DIVISION_LEAD"})
        and "strategy.observation.verify" in principal.permissions
        and target.lifecycle_state is LifecycleState.ACTIVE
    ):
        actions.append("VERIFY_MONITORING")
    state = performance_state(target, observations)
    data = project(target)
    data["performance_state"] = state
    updated = [
        target.updated_at,
        *(item.recorded_at for item in observations if item.recorded_at is not None),
    ]
    return {
        "target": data,
        "observations": project_domains(observations),
        "relationships": project_domains(
            tuple(
                item
                for item in relationships
                if (item.parent_target_id, item.parent_target_version)
                == (target.target_id, target.version)
                or (item.child_target_id, item.child_target_version)
                == (target.target_id, target.version)
            )
        ),
        "revisions": project_domains(revisions),
        "selected_observations": {
            key: project(selected.get(kind))
            for key, kind in (
                ("target", ObservationKind.TARGET),
                ("actual", ObservationKind.ACTUAL),
                ("forecast", ObservationKind.FORECAST),
            )
        },
        "performance_state": state,
        "authorized_actions": actions,
        "last_updated_at": project(max(updated)),
    }


async def overview(service: StrategyService, principal: Principal) -> dict[str, Any]:
    plans = tuple(
        item for item in await service.list_plans(principal) if item.scope.type == "COMPANY"
    )
    all_targets = tuple(
        item for item in await service.list_targets(principal) if item.scope.type == "COMPANY"
    )
    latest: dict[str, Target] = {}
    for target in all_targets:
        if target.target_id not in latest or latest[target.target_id].version < target.version:
            latest[target.target_id] = target
    targets = tuple(
        item
        for item in all_targets
        if item == latest[item.target_id] or item.lifecycle_state is LifecycleState.ACTIVE
    )
    objectives: list[Objective] = []
    for plan in plans:
        objectives.extend(
            item
            for item in await service.repository.list_objectives(plan.plan_id)
            if item.plan_version == plan.version
            and item.tenant_id == principal.tenant_id
            and item.organization_id == principal.organization_id
            and item.scope.type == "COMPANY"
        )
    assumptions = tuple(
        item
        for item in await service.repository.list_assumptions(
            principal.tenant_id, principal.organization_id
        )
        if item.scope.type == "COMPANY"
    )
    latest_assumptions = {
        item.assumption_id: max(
            (value for value in assumptions if value.assumption_id == item.assumption_id),
            key=lambda value: value.version,
        )
        for item in assumptions
    }
    details = [
        await target_detail(service, principal, target)
        for target in sorted(targets, key=lambda item: (item.target_id, item.version))
    ]
    times: list[datetime] = [item.updated_at for item in plans]
    times += [item.updated_at for item in all_targets]
    times += [item.created_at for item in objectives if item.created_at is not None]
    times += [item.updated_at for item in assumptions if item.updated_at is not None]
    for target in all_targets:
        times += [
            item.recorded_at
            for item in await service.repository.list_observations(target.target_id, target.version)
            if item.recorded_at is not None
        ]
    sorted_plans = sorted(plans, key=lambda item: (item.plan_id, item.version))
    return {
        "active_strategic_plans": [
            plan_projection(item, principal)
            for item in sorted_plans
            if item.plan_type.value == "STRATEGIC_PLAN"
            and item.lifecycle_state is LifecycleState.ACTIVE
        ],
        "active_operating_plans": [
            plan_projection(item, principal)
            for item in sorted_plans
            if item.plan_type.value == "OPERATING_PLAN"
            and item.lifecycle_state is LifecycleState.ACTIVE
        ],
        "plans": [plan_projection(item, principal) for item in sorted_plans],
        "objectives": project_domains(tuple(objectives)),
        "targets": details,
        "assumptions": project_domains(list(latest_assumptions.values())),
        "last_updated_at": project(max(times)) if times else None,
    }
