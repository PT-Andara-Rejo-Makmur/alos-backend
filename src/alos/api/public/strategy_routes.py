"""Contract-first HTTP boundary for Backend-owned strategy planning."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, Request

from alos.api.public.strategy_contracts import (
    BusinessTargetCreateRequest,
    CascadeAcceptRequest,
    CascadePreviewRequest,
    MetricObservationCreateRequest,
    PlanningAssumptionCreateRequest,
    StrategicObjectiveCreateRequest,
    StrategyPlanCreateRequest,
    StrategyPlanUpdateRequest,
    TargetRelationshipCreateRequest,
    TargetRevisionCreateRequest,
    request_contract,
)
from alos.dependencies import ContractCatalogDependency, CurrentPrincipalDependency
from alos.domains.strategy.models import (
    CascadeRule,
    Constraint,
    LifecycleState,
    Objective,
    Observation,
    ObservationKind,
    Period,
    Plan,
    PlanningAssumption,
    PlanType,
    RelationshipType,
    RuleType,
    ScopeRef,
    Target,
    TargetRelationship,
    VerificationState,
)
from alos.domains.strategy.projections import project, project_domains
from alos.domains.strategy.service import StrategyService
from alos.observability.correlation import current_correlation_id
from alos.security.errors import PlatformError

router = APIRouter(prefix="/api/v1/strategy", tags=["strategy"])


def _service(request: Request) -> StrategyService:
    return cast(StrategyService, request.app.state.strategy_service)


def _period(value: dict[str, Any]) -> Period:
    return Period(str(value["granularity"]), str(value["starts_at"]), str(value["ends_at"]))


def _scope(value: dict[str, Any]) -> ScopeRef:
    return ScopeRef(str(value["type"]), None if value.get("ref") is None else str(value["ref"]))


def _plan(payload: dict[str, Any], principal: CurrentPrincipalDependency) -> Plan:
    return Plan(
        plan_id=str(payload["plan_id"]),
        version=int(payload.get("version", 1)),
        plan_type=PlanType(str(payload["plan_type"])),
        name=str(payload["name"]),
        tenant_id=principal.tenant_id,
        organization_id=principal.organization_id,
        owner_workspace_id=str(payload.get("owner_workspace_id", principal.workspace_id)),
        owner_role_ref=str(payload["owner_role_ref"]),
        period=_period(payload["period"]),
        scope=_scope(payload["scope"]),
        lifecycle_state=LifecycleState.DRAFT,
        created_by=principal.actor_id,
        correlation_id=current_correlation_id(),
        strategic_plan_id=payload.get("strategic_plan_id"),
        strategic_plan_version=payload.get("strategic_plan_version"),
        description=payload.get("description"),
        materiality=str(payload.get("materiality", "NON_MATERIAL")),
        source_refs=tuple(payload.get("source_refs", ())),
        evidence_refs=tuple(payload.get("evidence_refs", ())),
    )


def _target(
    payload: dict[str, Any],
    principal: CurrentPrincipalDependency,
    *,
    cascade_run_id: str | None = None,
) -> Target:
    plan_ref = payload["plan_ref"]
    objective_ref = payload.get("objective_ref")
    return Target(
        target_id=str(payload["target_id"]),
        version=int(payload.get("version", 1)),
        code=str(payload["code"]),
        name=str(payload["name"]),
        plan_id=str(plan_ref["id"]),
        plan_version=int(plan_ref["version"]),
        objective_id=(None if objective_ref is None else str(objective_ref["id"])),
        objective_version=(None if objective_ref is None else int(objective_ref["version"])),
        tenant_id=principal.tenant_id,
        organization_id=principal.organization_id,
        owner_workspace_id=str(payload.get("owner_workspace_id", principal.workspace_id)),
        owner_role_ref=str(payload["owner_role_ref"]),
        measurement_type=str(payload["measurement_type"]),
        unit=str(payload["unit"]),
        period=_period(payload["period"]),
        scope=_scope(payload["scope"]),
        lifecycle_state=LifecycleState.DRAFT,
        created_by=principal.actor_id,
        correlation_id=current_correlation_id(),
        description=payload.get("description"),
        metric_code=payload.get("metric_code"),
        materiality=str(payload.get("materiality", "NON_MATERIAL")),
        source_refs=tuple(payload.get("source_refs", ())),
        evidence_refs=tuple(payload.get("evidence_refs", ())),
        cascade_run_id=cascade_run_id,
    )


def _plan_projection(plan: Plan, principal: CurrentPrincipalDependency) -> dict[str, Any]:
    data = project(plan)
    if data.get("strategic_plan_id") is None:
        data.pop("strategic_plan_id", None)
        data.pop("strategic_plan_version", None)
    actions: list[str] = []
    company_authority = (
        plan.scope.type == "COMPANY"
        and "EXECUTIVE" in principal.roles
        and "strategy.company.manage" in principal.permissions
    )
    division_authority = (
        plan.scope.type != "COMPANY"
        and "DIVISION_LEAD" in principal.roles
        and "strategy.division.manage" in principal.permissions
        and plan.owner_workspace_id == principal.workspace_id
    )
    if plan.lifecycle_state is LifecycleState.DRAFT and (company_authority or division_authority):
        actions.extend(("EDIT", "SUBMIT"))
    if (
        plan.lifecycle_state is LifecycleState.UNDER_REVIEW
        and "EXECUTIVE" in principal.roles
        and "strategy.approve" in principal.permissions
    ):
        actions.append("APPROVE")
    if (
        plan.lifecycle_state is LifecycleState.APPROVED
        and "EXECUTIVE" in principal.roles
        and "strategy.activate" in principal.permissions
    ):
        actions.append("ACTIVATE")
    data["authorized_actions"] = project(actions)
    return data


@router.get("/authority")
async def get_strategy_authority(
    request: Request, principal: CurrentPrincipalDependency
) -> dict[str, list[str]]:
    return _service(request).authority_projection(principal)


@router.get("/plans")
async def list_plans(
    request: Request, principal: CurrentPrincipalDependency
) -> list[dict[str, Any]]:
    return [
        _plan_projection(plan, principal) for plan in await _service(request).list_plans(principal)
    ]


@router.post("/plans", status_code=201, openapi_extra=request_contract(StrategyPlanCreateRequest))
async def create_plan(
    body: StrategyPlanCreateRequest,
    request: Request,
    contracts: ContractCatalogDependency,
    principal: CurrentPrincipalDependency,
) -> dict[str, Any]:
    payload = body.validated(contracts)
    plan = await _service(request).create_plan(principal, _plan(payload, principal))
    return _plan_projection(plan, principal)


@router.get("/plans/{plan_id}")
async def get_plan(
    plan_id: str, request: Request, principal: CurrentPrincipalDependency
) -> dict[str, Any]:
    return _plan_projection(await _service(request).get_plan(principal, plan_id), principal)


@router.patch("/plans/{plan_id}", openapi_extra=request_contract(StrategyPlanUpdateRequest))
async def update_plan(
    plan_id: str,
    body: StrategyPlanUpdateRequest,
    request: Request,
    contracts: ContractCatalogDependency,
    principal: CurrentPrincipalDependency,
) -> dict[str, Any]:
    payload = body.validated(contracts)
    changes: dict[str, Any] = {}
    for field in ("name", "description", "owner_role_ref", "materiality"):
        if field in payload:
            changes[field] = payload[field]
    if "period" in payload:
        changes["period"] = _period(payload["period"])
    for field in ("source_refs", "evidence_refs"):
        if field in payload:
            changes[field] = tuple(payload[field])
    return _plan_projection(
        await _service(request).update_plan(principal, plan_id, changes), principal
    )


@router.get("/objectives")
async def list_objectives(
    plan_id: str, request: Request, principal: CurrentPrincipalDependency
) -> list[dict[str, Any]]:
    await _service(request).get_plan(principal, plan_id)
    return project_domains(await _service(request).repository.list_objectives(plan_id))


@router.post(
    "/objectives", status_code=201, openapi_extra=request_contract(StrategicObjectiveCreateRequest)
)
async def create_objective(
    body: StrategicObjectiveCreateRequest,
    request: Request,
    contracts: ContractCatalogDependency,
    principal: CurrentPrincipalDependency,
) -> dict[str, Any]:
    payload = body.validated(contracts)
    objective = Objective(
        str(payload["objective_id"]),
        int(payload.get("version", 1)),
        str(payload["plan_id"]),
        int(payload["plan_version"]),
        str(payload["code"]),
        str(payload["name"]),
        principal.tenant_id,
        principal.organization_id,
        str(payload.get("workspace_id", principal.workspace_id)),
        principal.actor_id,
        current_correlation_id(),
        payload.get("description"),
        _scope(payload["scope"]),
        str(payload["owner_role_ref"]),
        LifecycleState.DRAFT,
    )
    return project(await _service(request).create_objective(principal, objective))


@router.get("/targets")
async def list_targets(
    request: Request, principal: CurrentPrincipalDependency
) -> list[dict[str, Any]]:
    return project_domains(await _service(request).list_targets(principal))


@router.post(
    "/targets", status_code=201, openapi_extra=request_contract(BusinessTargetCreateRequest)
)
async def create_target(
    body: BusinessTargetCreateRequest,
    request: Request,
    contracts: ContractCatalogDependency,
    principal: CurrentPrincipalDependency,
) -> dict[str, Any]:
    payload = body.validated(contracts)
    return project(await _service(request).create_target(principal, _target(payload, principal)))


@router.get("/targets/{target_id}")
async def get_target(
    target_id: str, request: Request, principal: CurrentPrincipalDependency
) -> dict[str, Any]:
    target = await _service(request).get_target(principal, target_id)
    relationships = await _service(request).repository.list_relationships(
        target.tenant_id, target.organization_id
    )
    revisions = await _service(request).repository.list_revisions(target.target_id)
    return {
        "target": project(target),
        "observations": project_domains(
            await _service(request).repository.list_observations(target.target_id, target.version)
        ),
        "relationships": project_domains(
            tuple(
                item
                for item in relationships
                if target.target_id in {item.parent_target_id, item.child_target_id}
            )
        ),
        "revisions": project_domains(revisions),
    }


@router.get("/targets/{target_id}/observations")
async def list_observations(
    target_id: str, request: Request, principal: CurrentPrincipalDependency
) -> list[dict[str, Any]]:
    target = await _service(request).get_target(principal, target_id)
    return project_domains(
        await _service(request).repository.list_observations(target.target_id, target.version)
    )


@router.post(
    "/targets/{target_id}/observations",
    status_code=201,
    openapi_extra=request_contract(MetricObservationCreateRequest),
)
async def create_observation(
    target_id: str,
    body: MetricObservationCreateRequest,
    request: Request,
    contracts: ContractCatalogDependency,
    principal: CurrentPrincipalDependency,
) -> dict[str, Any]:
    payload = body.validated(contracts)
    target = await _service(request).get_target(principal, target_id)
    if target_id != str(payload["target_id"]) or target.version != int(payload["target_version"]):
        raise PlatformError(
            "STRATEGY_PATH_MISMATCH",
            "Observation target reference differs from the requested target version.",
            status_code=409,
        )
    raw_value = payload.get("value")
    value = (
        raw_value if isinstance(raw_value, bool) or raw_value is None else Decimal(str(raw_value))
    )
    observation = Observation(
        observation_id=str(payload["observation_id"]),
        target_id=target.target_id,
        target_version=target.version,
        kind=ObservationKind(str(payload["kind"])),
        value=value,
        unit=str(payload["unit"]),
        period=_period(payload["period"]),
        source_ref=str(payload.get("source_ref") or "manual"),
        source_mode=str(payload["source_mode"]),
        observed_at=datetime.fromisoformat(str(payload["observed_at"]).replace("Z", "+00:00")),
        verified_at=(
            None
            if payload.get("verified_at") is None
            else datetime.fromisoformat(str(payload["verified_at"]).replace("Z", "+00:00"))
        ),
        verification_state=VerificationState(str(payload["verification_state"])),
        evidence_refs=tuple(payload.get("evidence_refs", ())),
        actor_id=principal.actor_id,
        tenant_id=principal.tenant_id,
        organization_id=principal.organization_id,
        owner_workspace_id=target.owner_workspace_id,
        correlation_id=current_correlation_id(),
    )
    return project(await _service(request).create_observation(principal, observation))


@router.get("/targets/{target_id}/relationships")
async def list_relationships(
    target_id: str, request: Request, principal: CurrentPrincipalDependency
) -> list[dict[str, Any]]:
    target = await _service(request).get_target(principal, target_id)
    values = await _service(request).repository.list_relationships(
        target.tenant_id, target.organization_id
    )
    return project_domains(
        tuple(item for item in values if target_id in {item.parent_target_id, item.child_target_id})
    )


@router.post(
    "/targets/{target_id}/relationships",
    status_code=201,
    openapi_extra=request_contract(TargetRelationshipCreateRequest),
)
async def create_relationship(
    target_id: str,
    body: TargetRelationshipCreateRequest,
    request: Request,
    contracts: ContractCatalogDependency,
    principal: CurrentPrincipalDependency,
) -> dict[str, Any]:
    payload = body.validated(contracts)
    if target_id != str(payload["parent_target_id"]):
        raise PlatformError(
            "STRATEGY_PATH_MISMATCH",
            "Path target differs from relationship parent.",
            status_code=409,
        )
    relationship = TargetRelationship(
        str(payload["relationship_id"]),
        RelationshipType(str(payload["relationship_type"])),
        target_id,
        int(payload["parent_target_version"]),
        str(payload["child_target_id"]),
        int(payload["child_target_version"]),
        principal.tenant_id,
        principal.organization_id,
        principal.actor_id,
        current_correlation_id(),
    )
    return project(await _service(request).create_relationship(principal, relationship))


@router.get("/assumptions")
async def list_assumptions(
    request: Request, principal: CurrentPrincipalDependency
) -> list[dict[str, Any]]:
    _service(request).authority_projection(principal)
    values = await _service(request).repository.list_assumptions(
        principal.tenant_id, principal.organization_id
    )
    return project_domains(
        tuple(
            item
            for item in values
            if "EXECUTIVE" in principal.roles
            or item.scope.type == "COMPANY"
            or item.owner_workspace_id == principal.workspace_id
        )
    )


@router.post(
    "/assumptions", status_code=201, openapi_extra=request_contract(PlanningAssumptionCreateRequest)
)
async def create_assumption(
    body: PlanningAssumptionCreateRequest,
    request: Request,
    contracts: ContractCatalogDependency,
    principal: CurrentPrincipalDependency,
) -> dict[str, Any]:
    payload = body.validated(contracts)
    assumption = PlanningAssumption(
        str(payload["assumption_id"]),
        int(payload.get("version", 1)),
        str(payload["name"]),
        str(payload.get("category", "CUSTOM")),
        None if payload.get("value") is None else Decimal(str(payload["value"])),
        str(payload["unit"]),
        _period(payload["period"]),
        _scope(payload["scope"]),
        str(payload.get("source_ref") or "manual"),
        str(payload["source_mode"]),
        tuple(payload.get("evidence_refs", ())),
        VerificationState(str(payload.get("verification_state", "UNVERIFIED"))),
        str(payload["owner_role_ref"]),
        str(payload.get("owner_workspace_id", principal.workspace_id)),
        principal.tenant_id,
        principal.organization_id,
        principal.actor_id,
        current_correlation_id(),
        payload.get("description"),
        LifecycleState.DRAFT,
    )
    return project(await _service(request).create_assumption(principal, assumption))


@router.post("/cascade/preview", openapi_extra=request_contract(CascadePreviewRequest))
async def preview_cascade(
    body: CascadePreviewRequest,
    request: Request,
    contracts: ContractCatalogDependency,
    principal: CurrentPrincipalDependency,
) -> dict[str, Any]:
    payload = body.validated(contracts)
    for item in payload["rules"]:
        if (item["tenant_id"], item["organization_id"]) != (
            principal.tenant_id,
            principal.organization_id,
        ):
            raise PlatformError(
                "STRATEGY_SCOPE_DENIED",
                "Cascade rule is outside authenticated authority.",
                status_code=403,
            )
    rules = tuple(
        CascadeRule(
            str(item["cascade_rule_id"]),
            RuleType(str(item["rule_type"])),
            str(item["output_target_refs"][0]["target_id"]),
            dict(item.get("parameters", {})),
        )
        for item in payload.get("rules", ())
    )
    inputs = {
        rule_id: {
            key: None if value is None else Decimal(str(value)) for key, value in values.items()
        }
        for rule_id, values in payload.get("rule_inputs", {}).items()
    }
    constraints = tuple(
        Constraint(
            str(item["constraint_id"]),
            str(item["constraint_type"]),
            bool(item.get("critical", False)),
            None if item.get("required_value") is None else Decimal(str(item["required_value"])),
            None if item.get("available_value") is None else Decimal(str(item["available_value"])),
            bool(item.get("applies", True)),
        )
        for item in payload.get("constraints", ())
    )
    run = await _service(request).preview_cascade(
        principal,
        root_target_id=str(payload["root_target_ref"]["target_id"]),
        root_target_version=int(payload["root_target_ref"]["version"]),
        rules=rules,
        rule_inputs=inputs,
        constraints=constraints,
        correlation_id=current_correlation_id(),
        assumption_refs=tuple(str(item) for item in payload.get("assumption_refs", ())),
    )
    projected = project(run)
    results = cast(list[dict[str, Any]], projected["result_snapshot"])
    return {
        "cascade_run_id": run.cascade_run_id,
        "status": run.status.value,
        "root_target_ref": {"id": run.root_target_id, "version": run.root_target_version},
        "derived_targets": [],
        "calculation_trace": [item for item in results if "rule_id" in item],
        "assumptions_used": [
            item for item in run.assumption_snapshot.values() if isinstance(item, dict)
        ],
        "constraint_results": [
            {
                "constraint_id": item["constraint_id"],
                "result": item["outcome"],
                "critical": item["critical"],
                "message": item["message"],
                "evaluated_at": project(run.created_at),
            }
            for item in results
            if "constraint_id" in item
        ],
        "blocking_conditions": [
            item["message"]
            for item in results
            if item.get("outcome") in {"FAIL", "UNKNOWN"} and item.get("critical")
        ]
        + [
            f"Planning assumption {assumption_id} is missing or unverified."
            for assumption_id, item in run.assumption_snapshot.items()
            if item is None
        ],
        "input_hash": run.input_hash,
        "result_hash": run.result_hash,
    }


@router.get("/cascade-runs/{cascade_run_id}")
async def get_cascade_run(
    cascade_run_id: str, request: Request, principal: CurrentPrincipalDependency
) -> dict[str, Any]:
    run = await _service(request).repository.get_cascade_run(cascade_run_id)
    if run is None:
        raise PlatformError(
            "STRATEGY_CASCADE_NOT_FOUND", "Cascade run was not found.", status_code=404
        )
    await _service(request).get_target(principal, run.root_target_id)
    return project(run)


@router.post(
    "/cascade-runs/{cascade_run_id}/accept", openapi_extra=request_contract(CascadeAcceptRequest)
)
async def accept_cascade(
    cascade_run_id: str,
    body: CascadeAcceptRequest,
    request: Request,
    contracts: ContractCatalogDependency,
    principal: CurrentPrincipalDependency,
) -> dict[str, Any]:
    payload = body.validated(contracts)
    targets = tuple(
        _target(item, principal, cascade_run_id=cascade_run_id)
        for item in payload.get("derived_targets", ())
    )
    return project(await _service(request).accept_cascade(principal, cascade_run_id, targets))


@router.post("/plans/{plan_id}/submit")
async def submit_plan(
    plan_id: str, request: Request, principal: CurrentPrincipalDependency
) -> dict[str, Any]:
    return _plan_projection(await _service(request).submit_plan(principal, plan_id), principal)


@router.post("/plans/{plan_id}/approve")
async def approve_plan(
    plan_id: str, request: Request, principal: CurrentPrincipalDependency
) -> dict[str, Any]:
    return _plan_projection(await _service(request).approve_plan(principal, plan_id), principal)


@router.post("/plans/{plan_id}/activate")
async def activate_plan(
    plan_id: str, request: Request, principal: CurrentPrincipalDependency
) -> dict[str, Any]:
    return _plan_projection(await _service(request).activate_plan(principal, plan_id), principal)


@router.get("/targets/{target_id}/revisions")
async def list_revisions(
    target_id: str, request: Request, principal: CurrentPrincipalDependency
) -> list[dict[str, Any]]:
    await _service(request).get_target(principal, target_id)
    return project_domains(await _service(request).repository.list_revisions(target_id))


@router.post(
    "/targets/{target_id}/revisions",
    status_code=201,
    openapi_extra=request_contract(TargetRevisionCreateRequest),
)
async def create_revision(
    target_id: str,
    body: TargetRevisionCreateRequest,
    request: Request,
    contracts: ContractCatalogDependency,
    principal: CurrentPrincipalDependency,
) -> dict[str, Any]:
    payload = body.validated(contracts)
    revision, target = await _service(request).revise_target(
        principal, target_id, str(payload["reason"]), current_correlation_id()
    )
    return {"revision": project(revision), "target": project(target)}
