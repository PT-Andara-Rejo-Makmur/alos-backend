"""Backend-authoritative planning lifecycle and cascade orchestration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from functools import wraps
from math import isfinite
from typing import Any, cast
from uuid import uuid4

from alos.audit import AuditEvent, AuditSink
from alos.authentication.repository import WorkspaceState
from alos.domains.strategy.authority import authorize
from alos.domains.strategy.cascade import CascadeEngine
from alos.domains.strategy.constraints import activation_blockers, evaluate_constraint
from alos.domains.strategy.measurement import OBSERVATION_STATES, selected_observations
from alos.domains.strategy.models import (
    CascadeRule,
    CascadeRun,
    CascadeStatus,
    Constraint,
    ConstraintOutcome,
    LifecycleState,
    Objective,
    Observation,
    ObservationKind,
    Period,
    Plan,
    PlanningAssumption,
    RuleType,
    Target,
    TargetRelationship,
    TargetRevision,
    VerificationState,
)
from alos.domains.strategy.projections import target_request
from alos.domains.strategy.repository import StrategyRepository
from alos.identity import Principal
from alos.security.errors import PlatformError


def atomic[**P, R](operation: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    @wraps(operation)
    async def execute(*args: P.args, **kwargs: P.kwargs) -> R:
        service, actor = cast(StrategyService, args[0]), cast(Principal, args[1])
        if service._pending_audit.get() is not None:
            return await operation(*args, **kwargs)
        async with service.repository.transaction(actor.tenant_id, actor.organization_id):
            events: list[AuditEvent] = []
            token = service._pending_audit.set(events)
            try:
                result = await operation(*args, **kwargs)
                for event in events:
                    await service.audit.append(event)
                return result
            finally:
                service._pending_audit.reset(token)

    return execute


class StrategyService:
    def __init__(
        self,
        repository: StrategyRepository,
        audit: AuditSink,
        workspace_lookup: Callable[[str], Awaitable[WorkspaceState | None]] | None = None,
        domain_source_validator: Callable[[Observation, Target], Awaitable[bool]] | None = None,
        domain_actual_calculator: Callable[[Principal, Target, str], Awaitable[Observation]]
        | None = None,
    ) -> None:
        self.repository = repository
        self.audit = audit
        self.workspace_lookup = workspace_lookup
        self.domain_source_validator = domain_source_validator
        self.domain_actual_calculator = domain_actual_calculator
        self.engine = CascadeEngine()
        self._pending_audit: ContextVar[list[AuditEvent] | None] = ContextVar(
            "strategy_audit_events", default=None
        )

    @staticmethod
    def authority_projection(principal: Principal) -> dict[str, list[str]]:
        authorize(
            principal,
            "read",
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
        )
        actions: list[str] = []
        if "EXECUTIVE" in principal.roles and "strategy.company.manage" in principal.permissions:
            actions.append("CREATE_COMPANY_PLAN")
        if (
            "DIVISION_LEAD" in principal.roles
            and "strategy.division.manage" in principal.permissions
        ):
            actions.append("CREATE_DIVISION_PLAN")
        verification: list[str] = []
        if "EXECUTIVE" in principal.roles and "strategy.review" in principal.permissions:
            verification.append("VERIFY_PLANNING")
        if (
            principal.roles.intersection({"EXECUTIVE", "DIVISION_LEAD"})
            and "strategy.observation.verify" in principal.permissions
        ):
            verification.append("VERIFY_MONITORING")
        return {"authorized_actions": actions, "verification_actions": verification}

    @atomic
    async def create_plan(self, principal: Principal, plan: Plan) -> Plan:
        action = "company_manage" if plan.scope.type == "COMPANY" else "division_manage"
        authorize(
            principal,
            action,
            tenant_id=plan.tenant_id,
            organization_id=plan.organization_id,
            owner_workspace_id=plan.owner_workspace_id,
        )
        if plan.lifecycle_state is not LifecycleState.DRAFT:
            raise PlatformError(
                "STRATEGY_LIFECYCLE_INVALID", "New plans must start in DRAFT.", status_code=409
            )
        self._workspace(principal, plan.owner_workspace_id)
        self._period_valid(plan.period.starts_at, plan.period.ends_at)
        existing = await self.repository.get_plan(plan.plan_id)
        if existing is not None:
            self._scope_match(
                existing, plan.tenant_id, plan.organization_id, plan.owner_workspace_id
            )
            if (existing.plan_type, existing.scope) != (plan.plan_type, plan.scope):
                raise PlatformError(
                    "STRATEGY_LINEAGE_CONFLICT",
                    "Plan identity and scope are immutable across versions.",
                    status_code=409,
                )
        if plan.strategic_plan_id is not None:
            parent = await self._plan(plan.strategic_plan_id, plan.strategic_plan_version)
            self._scope_match(parent, plan.tenant_id, plan.organization_id, plan.owner_workspace_id)
            if parent.plan_type.value != "STRATEGIC_PLAN" or not self._period_contains(
                parent.period, plan.period
            ):
                raise PlatformError(
                    "STRATEGY_PARENT_INVALID",
                    "Operating plan requires a matching strategic plan and contained period.",
                    status_code=409,
                )
        if existing is not None and plan.version <= existing.version:
            raise PlatformError(
                "STRATEGY_VERSION_CONFLICT",
                "A plan version must be greater than every retained historical version.",
                status_code=409,
            )
        await self.repository.save_plan(plan)
        await self._audit(
            "PLAN_CREATED",
            "plan",
            plan.plan_id,
            principal,
            plan.correlation_id,
            version=plan.version,
        )
        return plan

    async def list_plans(self, principal: Principal) -> tuple[Plan, ...]:
        authorize(
            principal,
            "read",
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
        )
        plans = await self.repository.list_plans(principal.tenant_id, principal.organization_id)
        if "EXECUTIVE" in principal.roles:
            return plans
        return tuple(
            plan
            for plan in plans
            if plan.owner_workspace_id == principal.workspace_id or plan.scope.type == "COMPANY"
        )

    async def get_plan(
        self, principal: Principal, plan_id: str, version: int | None = None
    ) -> Plan:
        plan = await self._plan(plan_id, version)
        authorize(principal, "read", tenant_id=plan.tenant_id, organization_id=plan.organization_id)
        if (
            "EXECUTIVE" not in principal.roles
            and plan.owner_workspace_id != principal.workspace_id
            and plan.scope.type != "COMPANY"
        ):
            raise PlatformError(
                "STRATEGY_WORKSPACE_DENIED", "Plan is outside active workspace.", status_code=403
            )
        return plan

    @atomic
    async def update_plan(
        self, principal: Principal, plan_id: str, changes: dict[str, Any]
    ) -> Plan:
        plan = await self._plan(plan_id)
        self._require_draft(plan)
        await self._authorize_plan_mutation(principal, plan)
        allowed = {
            "name",
            "description",
            "owner_role_ref",
            "period",
            "materiality",
            "source_refs",
            "evidence_refs",
        }
        if unknown := set(changes) - allowed:
            raise PlatformError(
                "STRATEGY_UPDATE_INVALID",
                "Plan update contains immutable or unsupported fields.",
                status_code=409,
                details={"fields": sorted(unknown)},
            )
        updated = replace(plan, **changes, updated_at=datetime.now(UTC))
        self._period_valid(updated.period.starts_at, updated.period.ends_at)
        await self.repository.save_plan(updated)
        await self._audit(
            "PLAN_UPDATED",
            "plan",
            plan.plan_id,
            principal,
            plan.correlation_id,
            version=plan.version,
        )
        return updated

    @atomic
    async def create_objective(self, principal: Principal, objective: Objective) -> Objective:
        plan = await self._plan(objective.plan_id, objective.plan_version)
        self._require_draft(plan)
        await self._authorize_plan_mutation(principal, plan)
        self._scope_match(
            plan, objective.tenant_id, objective.organization_id, objective.owner_workspace_id
        )
        if (objective.scope.type, objective.scope.ref) != (
            plan.scope.type,
            plan.scope.ref,
        ) or objective.lifecycle_state is not LifecycleState.DRAFT:
            raise PlatformError(
                "STRATEGY_SCOPE_DENIED",
                "Objective must share its draft plan scope.",
                status_code=403,
            )
        if any(
            item.objective_id == objective.objective_id and item.version == objective.version
            for item in await self.repository.list_objectives(plan.plan_id)
        ):
            raise PlatformError(
                "STRATEGY_HISTORY_CONFLICT", "Objective version already exists.", status_code=409
            )
        previous = await self.repository.get_objective(objective.objective_id)
        if previous is not None and (
            objective.version <= previous.version
            or (
                objective.tenant_id,
                objective.organization_id,
                objective.owner_workspace_id,
                objective.plan_id,
            )
            != (
                previous.tenant_id,
                previous.organization_id,
                previous.owner_workspace_id,
                previous.plan_id,
            )
        ):
            raise PlatformError(
                "STRATEGY_LINEAGE_CONFLICT",
                "Objective identity must retain its authority and owning plan.",
                status_code=409,
            )
        objective = replace(objective, created_at=datetime.now(UTC))
        await self.repository.save_objective(objective)
        await self._audit(
            "OBJECTIVE_CREATED",
            "objective",
            objective.objective_id,
            principal,
            objective.correlation_id,
            version=objective.version,
        )
        return objective

    @atomic
    async def create_target(self, principal: Principal, target: Target) -> Target:
        plan = await self._plan(target.plan_id, target.plan_version)
        self._require_draft(plan)
        action = "company_manage" if target.scope.type == "COMPANY" else "division_manage"
        authorize(
            principal,
            action,
            tenant_id=target.tenant_id,
            organization_id=target.organization_id,
            owner_workspace_id=target.owner_workspace_id,
        )
        self._scope_match(plan, target.tenant_id, target.organization_id, plan.owner_workspace_id)
        if target.lifecycle_state is not LifecycleState.DRAFT:
            raise PlatformError(
                "STRATEGY_LIFECYCLE_INVALID", "New targets must start in DRAFT.", status_code=409
            )
        if await self.repository.get_target(target.target_id, target.version) is not None:
            raise PlatformError(
                "STRATEGY_VERSION_CONFLICT",
                "The exact target version already exists.",
                status_code=409,
            )
        self._workspace(principal, target.owner_workspace_id)
        if target.owner_workspace_id != plan.owner_workspace_id or (
            target.scope.type,
            target.scope.ref,
        ) != (plan.scope.type, plan.scope.ref):
            raise PlatformError(
                "STRATEGY_SCOPE_DENIED",
                "Target scope must match its plan; workspace allocation requires cascade.",
                status_code=403,
            )
        await self._validate_target_definition(target, plan)
        historical = await self.repository.get_target(target.target_id)
        if historical is not None and (
            target.version <= historical.version
            or (
                target.tenant_id,
                target.organization_id,
                target.owner_workspace_id,
                target.scope,
                target.plan_id,
            )
            != (
                historical.tenant_id,
                historical.organization_id,
                historical.owner_workspace_id,
                historical.scope,
                historical.plan_id,
            )
        ):
            raise PlatformError(
                "STRATEGY_LINEAGE_CONFLICT",
                "New target version must preserve authority and lineage.",
                status_code=409,
            )
        await self.repository.save_target(target)
        await self._audit(
            "TARGET_CREATED",
            "target",
            target.target_id,
            principal,
            target.correlation_id,
            version=target.version,
        )
        return target

    @atomic
    async def create_observation(
        self, principal: Principal, observation: Observation
    ) -> Observation:
        target = await self._target(observation.target_id, observation.target_version)
        if target.lifecycle_state not in OBSERVATION_STATES[observation.kind]:
            raise PlatformError(
                "STRATEGY_IMMUTABLE",
                "Observation kind is not allowed in the target lifecycle state.",
                status_code=409,
            )
        authorize(
            principal,
            "company_manage" if target.scope.type == "COMPANY" else "division_manage",
            tenant_id=target.tenant_id,
            organization_id=target.organization_id,
            owner_workspace_id=target.owner_workspace_id,
        )
        self._workspace(principal, target.owner_workspace_id)
        plan = await self._plan(target.plan_id, target.plan_version)
        if target.lifecycle_state is LifecycleState.DRAFT and plan.lifecycle_state not in {
            LifecycleState.DRAFT,
            LifecycleState.ACTIVE,
        }:
            raise PlatformError(
                "STRATEGY_IMMUTABLE",
                "Planning observations are frozen while the plan is under review or approved.",
                status_code=409,
            )
        if (
            target.lifecycle_state is LifecycleState.DRAFT
            and plan.lifecycle_state is LifecycleState.ACTIVE
        ):
            if not any(
                item.to_version == target.version
                for item in await self.repository.list_revisions(target.target_id)
            ):
                raise PlatformError(
                    "STRATEGY_REVISION_REQUIRED",
                    "Planning changes to an active plan require a target revision.",
                    status_code=409,
                )
        await self._validate_observation_source(observation, target)
        self._validate_observation_value(observation, target)
        if observation.actor_id != principal.actor_id:
            raise PlatformError(
                "STRATEGY_ACTOR_DENIED",
                "Observation actor must be the authenticated actor.",
                status_code=403,
            )
        if observation.verification_state in {
            VerificationState.VERIFIED,
            VerificationState.CONFLICT,
            VerificationState.REJECTED,
        }:
            self._authorize_verification(principal, target, observation.kind)
        history = await self.repository.list_observations(target.target_id, target.version)
        observation = replace(
            observation,
            record_sequence=max((item.record_sequence or 0 for item in history), default=0) + 1,
            recorded_at=datetime.now(UTC),
            verified_at=datetime.now(UTC)
            if observation.verification_state is VerificationState.VERIFIED
            else None,
        )
        if observation.source_mode == "MANUAL_EVIDENCED" and not observation.evidence_refs:
            raise PlatformError(
                "STRATEGY_EVIDENCE_REQUIRED",
                "Manual evidenced observation requires evidence references.",
                status_code=409,
            )
        if (
            observation.tenant_id != target.tenant_id
            or observation.organization_id != target.organization_id
            or observation.owner_workspace_id != target.owner_workspace_id
        ):
            raise PlatformError(
                "STRATEGY_SCOPE_DENIED",
                "Observation scope differs from its target.",
                status_code=403,
            )
        await self.repository.save_observation(observation)
        await self._audit(
            "TARGET_OBSERVATION_CREATED",
            "target_observation",
            observation.observation_id,
            principal,
            observation.correlation_id,
            target_id=target.target_id,
            target_version=target.version,
            kind=observation.kind.value,
        )
        return observation

    @atomic
    async def derive_actual(
        self, principal: Principal, target_id: str, version: int, request_id: str
    ) -> Observation:
        target = await self.get_target(principal, target_id, version)
        authorize(
            principal,
            "division_manage",
            tenant_id=target.tenant_id,
            organization_id=target.organization_id,
            owner_workspace_id=target.owner_workspace_id,
        )
        if self.domain_actual_calculator is None:
            raise PlatformError(
                "STRATEGY_SOURCE_UNAVAILABLE", "Sumber otomatis tidak tersedia.", status_code=409
            )
        observation = await self.domain_actual_calculator(principal, target, request_id)
        history = await self.repository.list_observations(target_id, version)
        if any(item.observation_id == observation.observation_id for item in history):
            return observation
        return await self.create_observation(principal, observation)

    async def list_targets(self, principal: Principal) -> tuple[Target, ...]:
        authorize(
            principal,
            "read",
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
        )
        targets = await self.repository.list_targets(principal.tenant_id, principal.organization_id)
        if "EXECUTIVE" in principal.roles:
            return targets
        return tuple(
            target
            for target in targets
            if target.owner_workspace_id == principal.workspace_id or target.scope.type == "COMPANY"
        )

    async def get_target(
        self, principal: Principal, target_id: str, version: int | None = None
    ) -> Target:
        target = await self._target(target_id, version)
        authorize(
            principal, "read", tenant_id=target.tenant_id, organization_id=target.organization_id
        )
        if (
            "EXECUTIVE" not in principal.roles
            and target.owner_workspace_id != principal.workspace_id
            and target.scope.type != "COMPANY"
        ):
            raise PlatformError(
                "STRATEGY_WORKSPACE_DENIED", "Target is outside active workspace.", status_code=403
            )
        return target

    @atomic
    async def create_relationship(
        self, principal: Principal, relationship: TargetRelationship
    ) -> TargetRelationship:
        if relationship.parent_target_id == relationship.child_target_id:
            raise PlatformError(
                "STRATEGY_SELF_LINK", "A target cannot relate to itself.", status_code=409
            )
        parent = await self._target(
            relationship.parent_target_id, relationship.parent_target_version
        )
        child = await self._target(relationship.child_target_id, relationship.child_target_version)
        for target in (parent, child):
            if (
                target.tenant_id != relationship.tenant_id
                or target.organization_id != relationship.organization_id
            ):
                raise PlatformError(
                    "STRATEGY_SCOPE_DENIED",
                    "Target relationship crosses tenant or organization.",
                    status_code=403,
                )
        parent_plan = await self._plan(parent.plan_id, parent.plan_version)
        self._require_draft(parent_plan)
        await self._authorize_plan_mutation(principal, parent_plan)
        governed_assignment = False
        if child.cascade_run_id:
            linked_run = await self.repository.get_cascade_run(child.cascade_run_id)
            governed_assignment = bool(
                linked_run is not None
                and linked_run.status is CascadeStatus.ACCEPTED
                and (linked_run.root_target_id, linked_run.root_target_version)
                == (parent.target_id, parent.version)
            )
        if child.lifecycle_state is not LifecycleState.DRAFT or (
            child.owner_workspace_id != principal.workspace_id and not governed_assignment
        ):
            raise PlatformError(
                "STRATEGY_WORKSPACE_DENIED",
                "Foreign workspace relationships require a governed accepted cascade.",
                status_code=403,
            )
        relationships = await self.repository.list_relationships(
            relationship.tenant_id, relationship.organization_id
        )
        if any(
            r.relationship_type == relationship.relationship_type
            and (r.parent_target_id, r.parent_target_version)
            == (relationship.parent_target_id, relationship.parent_target_version)
            and (r.child_target_id, r.child_target_version)
            == (relationship.child_target_id, relationship.child_target_version)
            for r in relationships
        ):
            raise PlatformError(
                "STRATEGY_RELATIONSHIP_DUPLICATE", "Relationship already exists.", status_code=409
            )
        if relationship.relationship_type.value == "CASCADE" and self._creates_cycle(
            relationships, relationship
        ):
            raise PlatformError(
                "STRATEGY_CASCADE_CYCLE",
                "Cascade relationship would create a cycle.",
                status_code=409,
            )
        await self.repository.save_relationship(relationship)
        await self._audit(
            "RELATIONSHIP_CREATED",
            "target_relationship",
            relationship.relationship_id,
            principal,
            relationship.correlation_id,
        )
        return relationship

    @atomic
    async def create_assumption(
        self, principal: Principal, assumption: PlanningAssumption
    ) -> PlanningAssumption:
        action = "company_manage" if assumption.scope.type == "COMPANY" else "division_manage"
        authorize(
            principal,
            action,
            tenant_id=assumption.tenant_id,
            organization_id=assumption.organization_id,
            owner_workspace_id=assumption.owner_workspace_id,
        )
        self._workspace(principal, assumption.owner_workspace_id)
        self._period_valid(assumption.period.starts_at, assumption.period.ends_at)
        if assumption.source_mode == "SOURCE_LINKED":
            raise PlatformError(
                "STRATEGY_SOURCE_UNAVAILABLE",
                "Assumption source adapter is unavailable; use evidenced manual input.",
                status_code=409,
            )
        if assumption.verification_state in {
            VerificationState.VERIFIED,
            VerificationState.CONFLICT,
            VerificationState.REJECTED,
        }:
            authorize(
                principal,
                "review",
                tenant_id=assumption.tenant_id,
                organization_id=assumption.organization_id,
            )
        if assumption.lifecycle_state is not LifecycleState.DRAFT:
            raise PlatformError(
                "STRATEGY_LIFECYCLE_INVALID",
                "New assumptions must be draft versions.",
                status_code=409,
            )
        previous = await self.repository.get_assumption(assumption.assumption_id)
        if previous is not None and (
            assumption.version <= previous.version
            or (assumption.tenant_id, assumption.organization_id, assumption.owner_workspace_id)
            != (previous.tenant_id, previous.organization_id, previous.owner_workspace_id)
        ):
            raise PlatformError(
                "STRATEGY_LINEAGE_CONFLICT",
                "Assumption identity must retain scope and advance its version.",
                status_code=409,
            )
        assumption = replace(assumption, updated_at=datetime.now(UTC))
        if assumption.source_mode == "MANUAL_EVIDENCED" and not assumption.evidence_refs:
            raise PlatformError(
                "STRATEGY_EVIDENCE_REQUIRED",
                "Manual evidenced input requires evidence references.",
                status_code=409,
            )
        if assumption.value is None:
            raise PlatformError(
                "STRATEGY_ASSUMPTION_INCOMPLETE",
                "Planning assumptions require an explicit typed value.",
                status_code=409,
            )
        if (
            not isinstance(assumption.value, Decimal)
            or not assumption.value.is_finite()
            or not isfinite(float(assumption.value))
            or (assumption.unit == "RATIO" and not Decimal(0) <= assumption.value <= Decimal(1))
        ):
            raise PlatformError(
                "STRATEGY_ASSUMPTION_VALUE_INVALID",
                "Assumption must be finite and satisfy its canonical unit range.",
                status_code=409,
            )
        await self.repository.save_assumption(assumption)
        await self._audit(
            "ASSUMPTION_CREATED",
            "planning_assumption",
            assumption.assumption_id,
            principal,
            assumption.correlation_id,
            version=assumption.version,
        )
        if assumption.verification_state is VerificationState.VERIFIED:
            await self._audit(
                "ASSUMPTION_VERIFIED",
                "planning_assumption",
                assumption.assumption_id,
                principal,
                assumption.correlation_id,
                version=assumption.version,
            )
        return assumption

    @atomic
    async def preview_cascade(
        self,
        principal: Principal,
        *,
        root_target_id: str,
        root_target_version: int,
        rules: tuple[CascadeRule, ...],
        rule_inputs: dict[str, dict[str, Decimal | None]],
        constraints: tuple[Constraint, ...],
        correlation_id: str,
        assumption_refs: tuple[str, ...] = (),
        derived_targets: tuple[Target, ...] = (),
    ) -> CascadeRun:
        root = await self._target(root_target_id, root_target_version)
        authorize(principal, "read", tenant_id=root.tenant_id, organization_id=root.organization_id)
        await self.get_target(principal, root.target_id, root.version)
        self._workspace(principal, root.owner_workspace_id)
        root_plan = await self._plan(root.plan_id, root.plan_version)
        self._require_draft(root_plan)
        await self._authorize_plan_mutation(principal, root_plan)
        if (
            not rules
            or len({item.rule_id for item in rules}) != len(rules)
            or len({item.output_target_id for item in rules}) != len(rules)
        ):
            raise PlatformError(
                "STRATEGY_CASCADE_RULE_INVALID",
                "Rules require unique calculation and output identities.",
                status_code=409,
            )
        if derived_targets and {item.target_id for item in derived_targets} != {
            item.output_target_id for item in rules
        }:
            raise PlatformError(
                "STRATEGY_CASCADE_TARGET_INVALID",
                "Candidate identities must match all outputs.",
                status_code=409,
            )
        for candidate in derived_targets:
            await self._validate_cascade_candidate(principal, root, candidate)
        context = {"root": asdict(root), "plan": asdict(root_plan), "observations": {}}
        rule_inputs = {key: dict(value) for key, value in rule_inputs.items()}
        if set(rule_inputs) - {rule.rule_id for rule in rules}:
            raise PlatformError(
                "STRATEGY_CASCADE_INPUT_CONFLICT",
                "Inputs must reference a declared calculation rule.",
                status_code=409,
            )
        for rule in rules:
            inputs = rule_inputs.setdefault(rule.rule_id, {})
            refs = rule.input_target_refs or ((root.target_id, root.version),)
            if len(set(refs)) != len(refs) or (
                rule.rule_type is not RuleType.SUM_ROLLUP and len(refs) != 1
            ):
                raise PlatformError(
                    "STRATEGY_CASCADE_RULE_INVALID",
                    "Use unique inputs; only rollup accepts multiple source targets.",
                    status_code=409,
                )
            allowed = {
                RuleType.DIRECT: {"value"},
                RuleType.SPLIT_FIXED: {"parent", f"child:{rule.output_target_id}"},
                RuleType.SPLIT_PERCENT: {"parent", "ratio"}
                | {
                    f"share:{allocation['target_id']}"
                    for allocation in rule.parameters.get("allocations", [])
                },
                RuleType.SUM_ROLLUP: {f"child:{identity}" for identity, _version in refs},
                RuleType.RATIO_MULTIPLY: {"input", "ratio"},
                RuleType.RATIO_DIVIDE_CEIL: {"input", "ratio"},
                RuleType.LIMIT_CHECK: {"required", "available"},
            }[rule.rule_type]
            if set(inputs) - allowed or any(
                value is not None and not value.is_finite() for value in inputs.values()
            ):
                raise PlatformError(
                    "STRATEGY_CASCADE_INPUT_CONFLICT",
                    "Inputs must be finite and linked to declared sources or planning parameters.",
                    status_code=409,
                )
            for identity, version in refs:
                source = await self.get_target(principal, identity, version)
                if (
                    source.owner_workspace_id != principal.workspace_id
                    or source.plan_id != root.plan_id
                    or source.plan_version != root.plan_version
                ):
                    raise PlatformError(
                        "STRATEGY_SCOPE_DENIED",
                        "Cascade input targets must share the root plan and active workspace.",
                        status_code=403,
                    )
                selected = selected_observations(
                    await self.repository.list_observations(identity, version)
                ).get(ObservationKind.TARGET)
                if (
                    selected is not None
                    and selected.verification_state is VerificationState.VERIFIED
                    and isinstance(selected.value, Decimal)
                ):
                    key = (
                        "parent"
                        if rule.rule_type.value in {"SPLIT_PERCENT", "SPLIT_FIXED"}
                        else "value"
                        if rule.rule_type.value == "DIRECT"
                        else f"child:{identity}"
                        if rule.rule_type.value == "SUM_ROLLUP"
                        else "input"
                        if rule.rule_type.value.startswith("RATIO_")
                        else "required"
                    )
                    if key in inputs and inputs[key] is not None and inputs[key] != selected.value:
                        raise PlatformError(
                            "STRATEGY_CASCADE_INPUT_CONFLICT",
                            "Calculation input differs from its authoritative target observation.",
                            status_code=409,
                        )
                    inputs[key] = selected.value
                    context["observations"][f"{identity}:{version}"] = asdict(selected)
                else:
                    key = (
                        "parent"
                        if rule.rule_type.value in {"SPLIT_PERCENT", "SPLIT_FIXED"}
                        else "value"
                        if rule.rule_type.value == "DIRECT"
                        else f"child:{identity}"
                        if rule.rule_type.value == "SUM_ROLLUP"
                        else "input"
                        if rule.rule_type.value.startswith("RATIO_")
                        else "required"
                    )
                    inputs[key] = None
        available_assumptions = await self.repository.list_assumptions(
            root.tenant_id, root.organization_id
        )
        assumption_snapshot: dict[str, Any] = {}
        for assumption_id in assumption_refs:
            matching = [
                item for item in available_assumptions if item.assumption_id == assumption_id
            ]
            selected_assumption = max(matching, key=lambda item: item.version) if matching else None
            assumption_snapshot[assumption_id] = (
                None
                if selected_assumption is None
                or selected_assumption.value is None
                or selected_assumption.verification_state is not VerificationState.VERIFIED
                or (
                    selected_assumption.owner_workspace_id != principal.workspace_id
                    and selected_assumption.scope.type != "COMPANY"
                )
                else asdict(selected_assumption)
            )
        assumptions_incomplete = any(value is None for value in assumption_snapshot.values())
        for rule in rules:
            ratio_assumption_id = rule.parameters.get("ratio_assumption_id")
            if ratio_assumption_id is not None:
                chosen = assumption_snapshot.get(str(ratio_assumption_id))
                if chosen is None:
                    rule_inputs[rule.rule_id]["ratio"] = None
                    assumptions_incomplete = True
                else:
                    value = Decimal(str(chosen["value"]))
                    provided = rule_inputs[rule.rule_id].get("ratio")
                    if provided is not None and provided != value:
                        raise PlatformError(
                            "STRATEGY_CASCADE_INPUT_CONFLICT",
                            "Ratio differs from its verified assumption.",
                            status_code=409,
                        )
                    rule_inputs[rule.rule_id]["ratio"] = value
        for rule in rules:
            allocations = rule.parameters.get("allocations", [])
            if allocations:
                chosen_share = next(
                    (
                        item["share"]
                        for item in allocations
                        if item["target_id"] == rule.output_target_id
                    ),
                    None,
                )
                if chosen_share is None:
                    raise PlatformError(
                        "STRATEGY_CASCADE_RULE_INVALID",
                        "Allocation must reference its exact output target.",
                        status_code=409,
                    )
                share = Decimal(str(chosen_share))
                current_ratio = rule_inputs[rule.rule_id].get("ratio")
                if current_ratio is not None and current_ratio != share:
                    raise PlatformError(
                        "STRATEGY_CASCADE_INPUT_CONFLICT",
                        "Allocation share differs from the calculation ratio.",
                        status_code=409,
                    )
                rule_inputs[rule.rule_id]["ratio"] = share
                for allocation in allocations:
                    rule_inputs[rule.rule_id][f"share:{allocation['target_id']}"] = Decimal(
                        str(allocation["share"])
                    )
            if rule.rule_type.value == "SPLIT_FIXED" and rule.parameters.get("value") is not None:
                amount = Decimal(str(rule.parameters["value"]))
                key = f"child:{rule.output_target_id}"
                if key in rule_inputs[rule.rule_id] and rule_inputs[rule.rule_id][key] != amount:
                    raise PlatformError(
                        "STRATEGY_CASCADE_INPUT_CONFLICT",
                        "Fixed allocation differs from its rule value.",
                        status_code=409,
                    )
                rule_inputs[rule.rule_id][key] = amount
        if any(
            value is not None and not value.is_finite()
            for values in rule_inputs.values()
            for value in values.values()
        ):
            raise PlatformError(
                "STRATEGY_CASCADE_INPUT_CONFLICT",
                "Calculation parameters must be finite.",
                status_code=409,
            )
        candidates = tuple(self._candidate_request(item) for item in derived_targets)
        traces = tuple(
            self.engine.calculate(rule, inputs=rule_inputs.get(rule.rule_id, {})) for rule in rules
        )
        constraint_results = tuple(evaluate_constraint(item) for item in constraints)
        for constraint, result in zip(constraints, constraint_results, strict=True):
            await self._audit(
                "CONSTRAINT_EVALUATED",
                "planning_constraint",
                constraint.constraint_id,
                principal,
                correlation_id,
                outcome=result.outcome.value,
                critical=result.critical,
            )
        if any(trace.status is CascadeStatus.INVALID for trace in traces):
            status = CascadeStatus.INVALID
        elif (
            assumptions_incomplete
            or any(trace.status is CascadeStatus.INCOMPLETE for trace in traces)
            or any(result.outcome is ConstraintOutcome.UNKNOWN for result in constraint_results)
        ):
            status = CascadeStatus.INCOMPLETE
        elif any(result.outcome is ConstraintOutcome.FAIL for result in constraint_results):
            status = CascadeStatus.INVALID
        else:
            status = CascadeStatus.VALID
        input_snapshot = {
            key: {name: None if value is None else str(value) for name, value in values.items()}
            for key, values in rule_inputs.items()
        }
        result_snapshot = tuple(asdict(trace) for trace in traces) + tuple(
            asdict(result) for result in constraint_results
        )
        run = CascadeRun(
            cascade_run_id=f"cascade.{uuid4().hex}",
            correlation_id=correlation_id,
            root_target_id=root.target_id,
            root_target_version=root.version,
            input_snapshot=input_snapshot,
            assumption_snapshot=assumption_snapshot,
            rule_snapshot=tuple(asdict(rule) for rule in rules),
            constraint_snapshot=tuple(asdict(item) for item in constraints),
            result_snapshot=result_snapshot,
            input_hash=self._hash(
                {
                    "inputs": input_snapshot,
                    "rules": tuple(asdict(rule) for rule in rules),
                    "assumptions": assumption_snapshot,
                    "constraints": tuple(asdict(item) for item in constraints),
                    "candidates": candidates,
                    "context": context,
                }
            ),
            result_hash=self._hash(result_snapshot),
            created_by=principal.actor_id,
            created_at=datetime.now(UTC),
            status=status,
            tenant_id=root.tenant_id,
            organization_id=root.organization_id,
            owner_workspace_id=root.owner_workspace_id,
            candidate_snapshot=candidates,
            context_snapshot=context,
        )
        await self.repository.save_cascade_run(run)
        await self._audit(
            "CASCADE_PREVIEWED",
            "cascade_run",
            run.cascade_run_id,
            principal,
            correlation_id,
            status=run.status.value,
        )
        return run

    @atomic
    async def accept_cascade(
        self,
        principal: Principal,
        run_id: str,
        derived_targets: tuple[Target, ...],
        input_hash: str | None = None,
        result_hash: str | None = None,
    ) -> CascadeRun:
        run = await self.repository.get_cascade_run(run_id)
        if run is None:
            raise PlatformError(
                "STRATEGY_CASCADE_NOT_FOUND", "Cascade run was not found.", status_code=404
            )
        authorize(
            principal,
            "company_manage",
            tenant_id=run.tenant_id,
            organization_id=run.organization_id,
            owner_workspace_id=run.owner_workspace_id,
        )
        self._workspace(principal, run.owner_workspace_id)
        expected_input_hash = self._hash(
            {
                "inputs": run.input_snapshot,
                "rules": run.rule_snapshot,
                "assumptions": run.assumption_snapshot,
                "constraints": run.constraint_snapshot,
                "candidates": run.candidate_snapshot,
                "context": run.context_snapshot,
            }
        )
        if (
            run.input_hash != expected_input_hash
            or run.result_hash != self._hash(run.result_snapshot)
            or (input_hash is not None and input_hash != run.input_hash)
            or (result_hash is not None and result_hash != run.result_hash)
        ):
            raise PlatformError(
                "STRATEGY_CASCADE_INTEGRITY_CONFLICT",
                "Preview hashes do not match the retained immutable inputs and results.",
                status_code=409,
            )
        if run.status is not CascadeStatus.VALID:
            raise PlatformError(
                "STRATEGY_CASCADE_NOT_ACCEPTABLE",
                "Only a VALID cascade may be accepted.",
                status_code=409,
            )
        root = await self._target(run.root_target_id, run.root_target_version)
        root_plan = await self._plan(root.plan_id, root.plan_version)
        self._require_draft(root_plan)
        if self._hash(run.context_snapshot.get("root")) != self._hash(asdict(root)) or self._hash(
            run.context_snapshot.get("plan")
        ) != self._hash(asdict(root_plan)):
            raise PlatformError(
                "STRATEGY_CASCADE_STALE",
                "Plan or root target changed after preview.",
                status_code=409,
            )
        for reference, snapshot in run.context_snapshot.get("observations", {}).items():
            identity, version = reference.rsplit(":", 1)
            current = selected_observations(
                await self.repository.list_observations(identity, int(version))
            ).get(ObservationKind.TARGET)
            if current is None or self._hash(asdict(current)) != self._hash(snapshot):
                raise PlatformError(
                    "STRATEGY_CASCADE_STALE",
                    "Input observation changed after preview.",
                    status_code=409,
                )
        assumptions = await self.repository.list_assumptions(run.tenant_id, run.organization_id)
        for identity, snapshot in run.assumption_snapshot.items():
            matches = [item for item in assumptions if item.assumption_id == identity]
            current_assumption = max(matches, key=lambda item: item.version) if matches else None
            if current_assumption is None or self._hash(asdict(current_assumption)) != self._hash(
                snapshot
            ):
                raise PlatformError(
                    "STRATEGY_CASCADE_STALE", "Assumption changed after preview.", status_code=409
                )
        if len({target.target_id for target in derived_targets}) != len(derived_targets):
            raise PlatformError(
                "STRATEGY_CASCADE_TARGET_INVALID",
                "Duplicate derived target identities are not allowed.",
                status_code=409,
            )
        if run.candidate_snapshot and self._hash(
            sorted(run.candidate_snapshot, key=lambda item: item["target_id"])
        ) != self._hash(
            sorted(
                (self._candidate_request(item) for item in derived_targets),
                key=lambda item: item["target_id"],
            )
        ):
            raise PlatformError(
                "STRATEGY_CASCADE_TARGET_INVALID",
                "Confirmed metadata differs from the preview candidates.",
                status_code=409,
            )
        for candidate in derived_targets:
            await self._validate_cascade_candidate(principal, root, candidate)
            output_rule = next(
                (
                    item
                    for item in run.rule_snapshot
                    if item["output_target_id"] == candidate.target_id
                ),
                None,
            )
            if output_rule is None or candidate.version != output_rule.get("output_version", 1):
                raise PlatformError(
                    "STRATEGY_CASCADE_TARGET_INVALID",
                    "Candidate version differs from the calculated output reference.",
                    status_code=409,
                )
        expected_targets = {
            str(item["output_target_id"])
            for item in run.result_snapshot
            if item.get("rule_id") is not None
            and item.get("status") in {CascadeStatus.VALID, CascadeStatus.VALID.value}
            and item.get("output") is not None
        }
        provided_targets = {target.target_id for target in derived_targets}
        if expected_targets != provided_targets:
            raise PlatformError(
                "STRATEGY_CASCADE_TARGET_INVALID",
                "Derived targets must exactly match every valid calculation output.",
                status_code=409,
                details={
                    "expected_target_ids": sorted(expected_targets),
                    "provided_target_ids": sorted(provided_targets),
                },
            )
        for target in derived_targets:
            if (
                target.tenant_id != run.tenant_id
                or target.organization_id != run.organization_id
                or target.plan_id != root.plan_id
                or target.plan_version != root.plan_version
                or target.lifecycle_state is not LifecycleState.DRAFT
            ):
                raise PlatformError(
                    "STRATEGY_CASCADE_TARGET_INVALID",
                    "Accepted cascade targets must be scoped DRAFT versions.",
                    status_code=409,
                )
            if await self.repository.get_target(target.target_id, target.version) is not None:
                raise PlatformError(
                    "STRATEGY_VERSION_CONFLICT",
                    "The exact derived target version already exists.",
                    status_code=409,
                )
            await self.repository.save_target(replace(target, cascade_run_id=run.cascade_run_id))
            await self._audit(
                "TARGET_CREATED",
                "target",
                target.target_id,
                principal,
                run.correlation_id,
                version=target.version,
                cascade_run_id=run.cascade_run_id,
            )
            trace = next(
                (
                    item
                    for item in run.result_snapshot
                    if item.get("output_target_id") == target.target_id
                    and item.get("output") is not None
                ),
                None,
            )
            if trace is not None:
                observation = Observation(
                    observation_id=f"observation.{uuid4().hex}",
                    target_id=target.target_id,
                    target_version=target.version,
                    kind=ObservationKind.TARGET,
                    value=Decimal(str(trace["output"])),
                    unit=target.unit,
                    period=target.period,
                    source_ref=run.cascade_run_id,
                    source_mode="SOURCE_LINKED",
                    observed_at=run.created_at,
                    verification_state=VerificationState.VERIFIED,
                    evidence_refs=target.evidence_refs,
                    actor_id=principal.actor_id,
                    verified_at=datetime.now(UTC),
                    recorded_at=datetime.now(UTC),
                    record_sequence=1,
                    tenant_id=target.tenant_id,
                    organization_id=target.organization_id,
                    owner_workspace_id=target.owner_workspace_id,
                    correlation_id=run.correlation_id,
                )
                await self.repository.save_observation(observation)
                await self._audit(
                    "OBSERVATION_CREATED",
                    "target_observation",
                    observation.observation_id,
                    principal,
                    run.correlation_id,
                    target_id=target.target_id,
                    target_version=target.version,
                    kind=ObservationKind.TARGET.value,
                    verification_state=VerificationState.VERIFIED.value,
                    source_ref=run.cascade_run_id,
                )
        accepted = replace(run, status=CascadeStatus.ACCEPTED)
        await self.repository.save_cascade_run(accepted)
        await self._audit(
            "CASCADE_ACCEPTED", "cascade_run", run.cascade_run_id, principal, run.correlation_id
        )
        return accepted

    @atomic
    async def revise_target(
        self, principal: Principal, target_id: str, reason: str, correlation_id: str
    ) -> tuple[TargetRevision, Target]:
        current = await self._target(target_id)
        authorize(
            principal,
            "company_manage" if current.scope.type == "COMPANY" else "division_manage",
            tenant_id=current.tenant_id,
            organization_id=current.organization_id,
            owner_workspace_id=current.owner_workspace_id,
        )
        self._workspace(principal, current.owner_workspace_id)
        if not reason.strip() or current.lifecycle_state is not LifecycleState.ACTIVE:
            raise PlatformError(
                "STRATEGY_REVISION_CONFLICT",
                "Revision requires an active latest target and a reason.",
                status_code=409,
            )
        revision = TargetRevision(
            f"revision.{uuid4().hex}",
            current.target_id,
            current.version,
            current.version + 1,
            reason,
            "PROPOSED",
            principal.actor_id,
            datetime.now(UTC),
            correlation_id,
            current.tenant_id,
            current.organization_id,
            current.owner_workspace_id,
            current.evidence_refs,
        )
        draft = replace(
            current,
            version=current.version + 1,
            lifecycle_state=LifecycleState.DRAFT,
            cascade_run_id=None,
            created_by=principal.actor_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            correlation_id=correlation_id,
        )
        await self.repository.save_revision(revision)
        await self.repository.save_target(draft)
        await self._audit(
            "TARGET_REVISION_CREATED",
            "target",
            target_id,
            principal,
            correlation_id,
            from_version=current.version,
            to_version=draft.version,
        )
        return revision, draft

    @atomic
    async def submit_plan(self, principal: Principal, plan_id: str) -> Plan:
        plan = await self._plan(plan_id)
        await self._authorize_plan_mutation(principal, plan)
        self._require_draft(plan)
        return await self._transition(
            plan, LifecycleState.UNDER_REVIEW, "PLAN_SUBMITTED", principal
        )

    @atomic
    async def approve_plan(self, principal: Principal, plan_id: str) -> Plan:
        plan = await self._plan(plan_id)
        authorize(
            principal, "approve", tenant_id=plan.tenant_id, organization_id=plan.organization_id
        )
        self._workspace(principal, plan.owner_workspace_id)
        if plan.lifecycle_state is not LifecycleState.UNDER_REVIEW:
            raise PlatformError(
                "STRATEGY_LIFECYCLE_CONFLICT",
                "Only UNDER_REVIEW plan may be approved.",
                status_code=409,
            )
        return await self._transition(plan, LifecycleState.APPROVED, "PLAN_APPROVED", principal)

    @atomic
    async def activate_plan(
        self, principal: Principal, plan_id: str, constraints: tuple[Constraint, ...] = ()
    ) -> Plan:
        plan = await self._plan(plan_id)
        authorize(
            principal, "activate", tenant_id=plan.tenant_id, organization_id=plan.organization_id
        )
        self._workspace(principal, plan.owner_workspace_id)
        if plan.lifecycle_state is not LifecycleState.APPROVED:
            raise PlatformError(
                "STRATEGY_LIFECYCLE_CONFLICT",
                "Only APPROVED plan may be activated.",
                status_code=409,
            )
        if not plan.evidence_refs:
            raise PlatformError(
                "STRATEGY_EVIDENCE_REQUIRED", "Activation requires evidence.", status_code=409
            )
        activation_results = tuple(evaluate_constraint(item) for item in constraints)
        for constraint, result in zip(constraints, activation_results, strict=True):
            await self._audit(
                "CONSTRAINT_EVALUATED",
                "planning_constraint",
                constraint.constraint_id,
                principal,
                plan.correlation_id,
                outcome=result.outcome.value,
                critical=result.critical,
            )
        blockers = activation_blockers(activation_results)
        if blockers:
            raise PlatformError(
                "STRATEGY_CONSTRAINT_BLOCKED",
                "Critical constraints must PASS before activation.",
                status_code=409,
                details={"constraints": [asdict(item) for item in blockers]},
            )
        relationships = await self.repository.list_relationships(
            plan.tenant_id, plan.organization_id
        )
        if self._graph_has_cycle(relationships):
            raise PlatformError(
                "STRATEGY_CASCADE_CYCLE", "Cascade graph contains a cycle.", status_code=409
            )
        targets = await self.repository.list_targets(plan.tenant_id, plan.organization_id)
        current_target_ids = {
            target.target_id
            for target in targets
            if target.plan_id == plan.plan_id and target.plan_version == plan.version
        }
        latest: dict[str, Target] = {}
        for target in targets:
            if target.plan_id == plan.plan_id and target.plan_version == plan.version:
                if (
                    target.target_id not in latest
                    or latest[target.target_id].version < target.version
                ):
                    latest[target.target_id] = target
        for target in latest.values():
            await self._validate_target_definition(target, plan)
            await self._target_readiness(target)
        for target in targets:
            if target in latest.values():
                if target.cascade_run_id is not None:
                    run = await self.repository.get_cascade_run(target.cascade_run_id)
                    if run is None or run.status is not CascadeStatus.ACCEPTED:
                        raise PlatformError(
                            "STRATEGY_CASCADE_BLOCKED",
                            "Derived targets require an ACCEPTED immutable cascade run.",
                            status_code=409,
                            details={"target_id": target.target_id},
                        )
                    unresolved = [
                        item
                        for item in run.result_snapshot
                        if item.get("status")
                        in {
                            CascadeStatus.INVALID,
                            CascadeStatus.INVALID.value,
                            CascadeStatus.INCOMPLETE,
                            CascadeStatus.INCOMPLETE.value,
                        }
                        or (
                            item.get("critical")
                            and item.get("outcome")
                            in {
                                ConstraintOutcome.FAIL,
                                ConstraintOutcome.FAIL.value,
                                ConstraintOutcome.UNKNOWN,
                                ConstraintOutcome.UNKNOWN.value,
                            }
                        )
                    ]
                    if unresolved:
                        raise PlatformError(
                            "STRATEGY_CASCADE_BLOCKED",
                            "Cascade calculations and critical constraints must be resolved.",
                            status_code=409,
                            details={"target_id": target.target_id, "results": unresolved},
                        )
                await self.repository.save_target(
                    replace(
                        target,
                        lifecycle_state=LifecycleState.ACTIVE,
                        updated_at=datetime.now(UTC),
                    )
                )
            elif (
                target.target_id in current_target_ids
                and target.lifecycle_state is LifecycleState.ACTIVE
            ):
                await self.repository.save_target(
                    replace(
                        target,
                        lifecycle_state=LifecycleState.SUPERSEDED,
                        updated_at=datetime.now(UTC),
                    )
                )
        plans = await self.repository.list_plans(plan.tenant_id, plan.organization_id)
        for previous in plans:
            if (
                previous.plan_id == plan.plan_id
                and previous.version != plan.version
                and previous.lifecycle_state is LifecycleState.ACTIVE
            ):
                await self._close_plan(previous, LifecycleState.SUPERSEDED, principal)
        return await self._transition(plan, LifecycleState.ACTIVE, "PLAN_ACTIVATED", principal)

    @atomic
    async def archive_plan(
        self, principal: Principal, plan_id: str, version: int | None = None
    ) -> Plan:
        plan = await self._plan(plan_id, version)
        authorize(
            principal, "activate", tenant_id=plan.tenant_id, organization_id=plan.organization_id
        )
        self._workspace(principal, plan.owner_workspace_id)
        if plan.lifecycle_state is not LifecycleState.ACTIVE:
            raise PlatformError(
                "STRATEGY_LIFECYCLE_CONFLICT",
                "Only an ACTIVE plan may be archived.",
                status_code=409,
            )
        return await self._close_plan(plan, LifecycleState.ARCHIVED, principal)

    async def _close_plan(self, plan: Plan, state: LifecycleState, principal: Principal) -> Plan:
        for target in await self.repository.list_targets(plan.tenant_id, plan.organization_id):
            if (target.plan_id, target.plan_version) == (
                plan.plan_id,
                plan.version,
            ) and target.lifecycle_state not in {
                LifecycleState.SUPERSEDED,
                LifecycleState.ARCHIVED,
            }:
                await self.repository.save_target(
                    replace(target, lifecycle_state=state, updated_at=datetime.now(UTC))
                )
        return await self._transition(plan, state, f"PLAN_{state.value}", principal)

    async def _transition(
        self, plan: Plan, state: LifecycleState, event: str, principal: Principal
    ) -> Plan:
        updated = plan.transition(state)
        for objective in await self.repository.list_objectives(plan.plan_id):
            if objective.plan_version == plan.version:
                await self.repository.save_objective(replace(objective, lifecycle_state=state))
        await self.repository.save_plan(updated)
        await self._audit(
            event, "plan", plan.plan_id, principal, plan.correlation_id, version=plan.version
        )
        return updated

    async def _authorize_plan_mutation(self, principal: Principal, plan: Plan) -> None:
        action = "company_manage" if plan.scope.type == "COMPANY" else "division_manage"
        authorize(
            principal,
            action,
            tenant_id=plan.tenant_id,
            organization_id=plan.organization_id,
            owner_workspace_id=plan.owner_workspace_id,
        )
        self._workspace(principal, plan.owner_workspace_id)

    @staticmethod
    def _candidate_request(target: Target) -> dict[str, Any]:
        return target_request(target)

    async def _validate_cascade_candidate(
        self, principal: Principal, root: Target, candidate: Target
    ) -> None:
        plan = await self._plan(root.plan_id, root.plan_version)
        if (
            (
                candidate.tenant_id,
                candidate.organization_id,
                candidate.plan_id,
                candidate.plan_version,
            )
            != (root.tenant_id, root.organization_id, root.plan_id, root.plan_version)
            or candidate.lifecycle_state is not LifecycleState.DRAFT
            or candidate.target_id == root.target_id
        ):
            raise PlatformError(
                "STRATEGY_CASCADE_TARGET_INVALID",
                "Candidate must be a new draft output of the exact root plan.",
                status_code=409,
            )
        if root.scope.type != "COMPANY" and candidate.owner_workspace_id != principal.workspace_id:
            raise PlatformError(
                "STRATEGY_WORKSPACE_DENIED",
                "Division cascade cannot assign another workspace.",
                status_code=403,
            )
        if candidate.owner_workspace_id != principal.workspace_id:
            authorize(
                principal,
                "company_manage",
                tenant_id=root.tenant_id,
                organization_id=root.organization_id,
            )
            workspace = (
                await self.workspace_lookup(candidate.owner_workspace_id)
                if self.workspace_lookup
                else None
            )
            if (
                workspace is None
                or not workspace.active
                or (workspace.tenant_id, workspace.organization_id)
                != (root.tenant_id, root.organization_id)
            ):
                raise PlatformError(
                    "STRATEGY_WORKSPACE_DENIED",
                    "Cascade workspace must be active and belong to the organization.",
                    status_code=403,
                )
        if candidate.scope.type != "COMPANY" and candidate.scope.ref not in {
            None,
            candidate.owner_workspace_id,
        }:
            raise PlatformError(
                "STRATEGY_SCOPE_DENIED",
                "Division scope reference must match its owner workspace.",
                status_code=403,
            )
        await self._validate_target_definition(candidate, plan)
        if await self.repository.get_target(candidate.target_id) is not None:
            raise PlatformError(
                "STRATEGY_VERSION_CONFLICT",
                "Cascade outputs require unused target identities.",
                status_code=409,
            )

    @staticmethod
    def _workspace(principal: Principal, workspace_id: str) -> None:
        if workspace_id != principal.workspace_id:
            raise PlatformError(
                "STRATEGY_WORKSPACE_DENIED",
                "Mutation must remain in the active workspace.",
                status_code=403,
            )

    @staticmethod
    def _period_valid(starts_at: str, ends_at: str) -> None:
        if starts_at > ends_at:
            raise PlatformError(
                "STRATEGY_PERIOD_INVALID", "Period start must not follow its end.", status_code=409
            )

    @staticmethod
    def _period_contains(parent: Period, child: Period) -> bool:
        return bool(parent.starts_at <= child.starts_at <= child.ends_at <= parent.ends_at)

    async def _validate_target_definition(self, target: Target, plan: Plan) -> None:
        self._period_valid(target.period.starts_at, target.period.ends_at)
        if not self._period_contains(plan.period, target.period):
            raise PlatformError(
                "STRATEGY_PERIOD_INVALID",
                "Target period must be contained in its plan.",
                status_code=409,
            )
        if target.objective_id is not None:
            objectives = await self.repository.list_objectives(plan.plan_id)
            if not any(
                item.objective_id == target.objective_id
                and item.version == target.objective_version
                and item.plan_version == plan.version
                and item.tenant_id == target.tenant_id
                and item.organization_id == target.organization_id
                for item in objectives
            ):
                raise PlatformError(
                    "STRATEGY_OBJECTIVE_INVALID",
                    "Objective reference must resolve to the exact owning plan version.",
                    status_code=409,
                )

    @staticmethod
    def _validate_observation_value(observation: Observation, target: Target) -> None:
        if observation.period != target.period or observation.unit != target.unit:
            raise PlatformError(
                "STRATEGY_MEASUREMENT_INVALID",
                "Observation unit and period must match the target version.",
                status_code=409,
            )
        value = observation.value
        if isinstance(value, Decimal) and not value.is_finite():
            raise PlatformError(
                "STRATEGY_MEASUREMENT_INVALID",
                "Observation must be finite or unknown.",
                status_code=409,
            )
        if value is not None and (isinstance(value, bool) != (target.unit == "BOOLEAN")):
            raise PlatformError(
                "STRATEGY_MEASUREMENT_INVALID",
                "Boolean and numeric units must retain their value type.",
                status_code=409,
            )
        if isinstance(value, Decimal) and (
            (target.unit == "RATIO" and not Decimal(0) <= value <= Decimal(1))
            or (target.unit == "PERCENT" and not Decimal(0) <= value <= Decimal(100))
        ):
            raise PlatformError(
                "STRATEGY_MEASUREMENT_INVALID",
                "Ratio or percentage is outside its scale.",
                status_code=409,
            )

    async def _validate_observation_source(self, observation: Observation, target: Target) -> None:
        if observation.source_mode == "MANUAL_EVIDENCED":
            if not observation.evidence_refs:
                raise PlatformError(
                    "STRATEGY_EVIDENCE_REQUIRED",
                    "Manual evidenced observation requires evidence references.",
                    status_code=409,
                )
            return
        if observation.kind is ObservationKind.ACTUAL and self.domain_source_validator is not None:
            if await self.domain_source_validator(observation, target):
                return
            raise PlatformError(
                "STRATEGY_SOURCE_CONFLICT",
                "Perhitungan sumber tidak cocok dengan observasi.",
                status_code=409,
            )
        run = (
            await self.repository.get_cascade_run(observation.source_ref)
            if observation.source_ref
            else None
        )
        if (
            run is None
            or run.status is not CascadeStatus.ACCEPTED
            or run.tenant_id != target.tenant_id
            or run.organization_id != target.organization_id
            or target.cascade_run_id != run.cascade_run_id
            or observation.kind is not ObservationKind.TARGET
        ):
            raise PlatformError(
                "STRATEGY_SOURCE_UNAVAILABLE",
                "SOURCE_LINKED needs an accepted cascade; external adapters are unavailable.",
                status_code=409,
            )
        trace = next(
            (
                item
                for item in run.result_snapshot
                if item.get("output_target_id") == target.target_id
            ),
            None,
        )
        if trace is None or str(observation.value) != str(trace.get("output")):
            raise PlatformError(
                "STRATEGY_SOURCE_CONFLICT",
                "Observation value differs from its authoritative cascade output.",
                status_code=409,
            )

    def _authorize_verification(
        self, principal: Principal, target: Target, kind: ObservationKind
    ) -> None:
        self._workspace(principal, target.owner_workspace_id)
        authorize(
            principal,
            "review"
            if kind in {ObservationKind.TARGET, ObservationKind.ASSUMPTION}
            else "verify_monitoring",
            tenant_id=target.tenant_id,
            organization_id=target.organization_id,
            owner_workspace_id=target.owner_workspace_id,
        )

    @atomic
    async def verify_observation(
        self,
        principal: Principal,
        target_id: str,
        observation_id: str,
        state: VerificationState,
        reason: str,
        correlation_id: str,
        version: int | None = None,
    ) -> Observation:
        target = await self.get_target(principal, target_id, version)
        observations = await self.repository.list_observations(target.target_id, target.version)
        original = next(
            (item for item in observations if item.observation_id == observation_id), None
        )
        if original is None:
            raise PlatformError(
                "STRATEGY_OBSERVATION_NOT_FOUND",
                "Observation was not found in this target version.",
                status_code=404,
            )
        self._authorize_verification(principal, target, original.kind)
        if not reason.strip() or state not in {
            VerificationState.VERIFIED,
            VerificationState.CONFLICT,
            VerificationState.REJECTED,
        }:
            raise PlatformError(
                "STRATEGY_VERIFICATION_INVALID",
                "A terminal verification state and reason are required.",
                status_code=409,
            )
        if original.verification_state in {
            VerificationState.VERIFIED,
            VerificationState.CONFLICT,
            VerificationState.REJECTED,
        } or any(
            item.supersedes_observation_id == original.observation_id for item in observations
        ):
            raise PlatformError(
                "STRATEGY_VERIFICATION_CONFLICT",
                "Observation already has a retained verification decision.",
                status_code=409,
            )
        if target.lifecycle_state in {LifecycleState.SUPERSEDED, LifecycleState.ARCHIVED}:
            raise PlatformError(
                "STRATEGY_IMMUTABLE", "Historical target verification is closed.", status_code=409
            )
        await self._validate_observation_source(original, target)
        verified = replace(
            original,
            observation_id=f"observation.{uuid4().hex}",
            verification_state=state,
            verified_at=datetime.now(UTC) if state is VerificationState.VERIFIED else None,
            recorded_at=datetime.now(UTC),
            actor_id=principal.actor_id,
            supersedes_observation_id=original.observation_id,
            record_sequence=max((item.record_sequence or 0 for item in observations), default=0)
            + 1,
            verification_reason=reason.strip(),
            correlation_id=correlation_id,
        )
        await self.repository.save_observation(verified)
        await self._audit(
            "OBSERVATION_VERIFICATION_RECORDED",
            "target_observation",
            verified.observation_id,
            principal,
            correlation_id,
            previous_observation_id=original.observation_id,
            verification_state=state.value,
            reason=reason.strip(),
        )
        return verified

    @atomic
    async def verify_assumption(
        self,
        principal: Principal,
        assumption_id: str,
        state: VerificationState,
        reason: str,
        correlation_id: str,
    ) -> PlanningAssumption:
        authorize(
            principal,
            "review",
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
        )
        values = await self.repository.list_assumptions(
            principal.tenant_id, principal.organization_id
        )
        matches = [item for item in values if item.assumption_id == assumption_id]
        if not matches:
            raise PlatformError(
                "STRATEGY_ASSUMPTION_NOT_FOUND", "Assumption was not found.", status_code=404
            )
        current = max(matches, key=lambda item: item.version)
        self._workspace(principal, current.owner_workspace_id)
        if not reason.strip() or state not in {
            VerificationState.VERIFIED,
            VerificationState.CONFLICT,
            VerificationState.REJECTED,
        }:
            raise PlatformError(
                "STRATEGY_VERIFICATION_INVALID",
                "A terminal verification state and reason are required.",
                status_code=409,
            )
        if current.verification_state in {
            VerificationState.VERIFIED,
            VerificationState.CONFLICT,
            VerificationState.REJECTED,
        }:
            raise PlatformError(
                "STRATEGY_VERIFICATION_CONFLICT",
                "Assumption already has a verification decision.",
                status_code=409,
            )
        updated = replace(
            current,
            version=current.version + 1,
            verification_state=state,
            updated_at=datetime.now(UTC),
            verification_reason=reason.strip(),
            created_by=principal.actor_id,
            correlation_id=correlation_id,
        )
        await self.repository.save_assumption(updated)
        await self._audit(
            "ASSUMPTION_VERIFICATION_RECORDED",
            "planning_assumption",
            assumption_id,
            principal,
            correlation_id,
            from_version=current.version,
            to_version=updated.version,
            verification_state=state.value,
            reason=reason.strip(),
        )
        return updated

    @atomic
    async def update_target(
        self, principal: Principal, target_id: str, version: int, changes: dict[str, Any]
    ) -> Target:
        target = await self.get_target(principal, target_id, version)
        authorize(
            principal,
            "company_manage" if target.scope.type == "COMPANY" else "division_manage",
            tenant_id=target.tenant_id,
            organization_id=target.organization_id,
            owner_workspace_id=target.owner_workspace_id,
        )
        self._workspace(principal, target.owner_workspace_id)
        if target.lifecycle_state is not LifecycleState.DRAFT:
            raise PlatformError(
                "STRATEGY_IMMUTABLE",
                "Only draft target definitions may be edited.",
                status_code=409,
            )
        plan = await self._plan(target.plan_id, target.plan_version)
        if plan.lifecycle_state not in {LifecycleState.DRAFT, LifecycleState.ACTIVE}:
            raise PlatformError(
                "STRATEGY_IMMUTABLE",
                "Planning target definitions are frozen during review.",
                status_code=409,
            )
        if plan.lifecycle_state is LifecycleState.ACTIVE and not any(
            item.to_version == target.version
            for item in await self.repository.list_revisions(target.target_id)
        ):
            raise PlatformError(
                "STRATEGY_REVISION_REQUIRED",
                "Active-plan edits require a retained revision.",
                status_code=409,
            )
        allowed = {
            "code",
            "name",
            "description",
            "metric_code",
            "measurement_type",
            "unit",
            "period",
            "materiality",
            "evidence_refs",
            "source_refs",
        }
        if set(changes) - allowed:
            raise PlatformError(
                "STRATEGY_UPDATE_INVALID",
                "Immutable target metadata cannot be changed.",
                status_code=409,
            )
        updated = replace(target, **changes, updated_at=datetime.now(UTC))
        await self._validate_target_definition(updated, plan)
        await self.repository.save_target(updated)
        await self._audit(
            "TARGET_UPDATED",
            "target",
            target.target_id,
            principal,
            target.correlation_id,
            version=target.version,
        )
        return updated

    @atomic
    async def transition_target(self, principal: Principal, target_id: str, action: str) -> Target:
        target = await self.get_target(principal, target_id)
        self._workspace(principal, target.owner_workspace_id)
        plan = await self._plan(target.plan_id, target.plan_version)
        if plan.lifecycle_state is not LifecycleState.ACTIVE or not any(
            item.to_version == target.version
            for item in await self.repository.list_revisions(target.target_id)
        ):
            raise PlatformError(
                "STRATEGY_REVISION_REQUIRED",
                "Independent target lifecycle requires a revision of an active plan.",
                status_code=409,
            )
        transitions = {
            "submit": (LifecycleState.DRAFT, LifecycleState.UNDER_REVIEW),
            "approve": (LifecycleState.UNDER_REVIEW, LifecycleState.APPROVED),
            "activate": (LifecycleState.APPROVED, LifecycleState.ACTIVE),
        }
        before, after = transitions[action]
        authorize(
            principal,
            ("company_manage" if target.scope.type == "COMPANY" else "division_manage")
            if action == "submit"
            else action,
            tenant_id=target.tenant_id,
            organization_id=target.organization_id,
            owner_workspace_id=target.owner_workspace_id,
        )
        if target.lifecycle_state is not before:
            raise PlatformError(
                "STRATEGY_LIFECYCLE_CONFLICT",
                "Target revision transition is not valid from this state.",
                status_code=409,
            )
        if action == "activate":
            await self._target_readiness(target)
            for historical in await self.repository.list_targets(
                target.tenant_id, target.organization_id
            ):
                if (
                    historical.target_id == target.target_id
                    and historical.version != target.version
                    and historical.lifecycle_state is LifecycleState.ACTIVE
                ):
                    await self.repository.save_target(
                        replace(
                            historical,
                            lifecycle_state=LifecycleState.SUPERSEDED,
                            updated_at=datetime.now(UTC),
                        )
                    )
        updated = replace(target, lifecycle_state=after, updated_at=datetime.now(UTC))
        await self.repository.save_target(updated)
        await self._audit(
            f"TARGET_{after.value}",
            "target",
            target_id,
            principal,
            target.correlation_id,
            version=target.version,
        )
        return updated

    async def _target_readiness(self, target: Target) -> None:
        observations = await self.repository.list_observations(target.target_id, target.version)
        planned = selected_observations(observations).get(ObservationKind.TARGET)
        if (
            planned is None
            or planned.verification_state is not VerificationState.VERIFIED
            or planned.value is None
            or planned.unit != target.unit
            or planned.period != target.period
        ):
            raise PlatformError(
                "STRATEGY_OBSERVATION_BLOCKED",
                "Every target requires a VERIFIED TARGET observation before activation.",
                status_code=409,
            )
        if target.cascade_run_id is not None:
            run = await self.repository.get_cascade_run(target.cascade_run_id)
            if run is None or run.status is not CascadeStatus.ACCEPTED:
                raise PlatformError(
                    "STRATEGY_CASCADE_BLOCKED",
                    "Derived targets require an ACCEPTED immutable cascade run.",
                    status_code=409,
                )

    @staticmethod
    def _require_draft(plan: Plan) -> None:
        if plan.lifecycle_state is not LifecycleState.DRAFT:
            raise PlatformError(
                "STRATEGY_IMMUTABLE", "Only DRAFT plans may be edited.", status_code=409
            )

    @staticmethod
    def _scope_match(plan: Plan, tenant: str, organization: str, workspace: str) -> None:
        if (tenant, organization, workspace) != (
            plan.tenant_id,
            plan.organization_id,
            plan.owner_workspace_id,
        ):
            raise PlatformError(
                "STRATEGY_SCOPE_DENIED", "Object scope differs from its plan.", status_code=403
            )

    async def _plan(self, plan_id: str, version: int | None = None) -> Plan:
        plan = await self.repository.get_plan(plan_id, version)
        if plan is None:
            raise PlatformError("STRATEGY_PLAN_NOT_FOUND", "Plan was not found.", status_code=404)
        return plan

    async def _target(self, target_id: str, version: int | None = None) -> Target:
        target = await self.repository.get_target(target_id, version)
        if target is None:
            raise PlatformError(
                "STRATEGY_TARGET_NOT_FOUND", "Target was not found.", status_code=404
            )
        return target

    @staticmethod
    def _creates_cycle(
        existing: tuple[TargetRelationship, ...], candidate: TargetRelationship
    ) -> bool:
        graph = [
            (r.parent_target_id, r.child_target_id)
            for r in existing
            if r.relationship_type.value == "CASCADE"
        ] + [(candidate.parent_target_id, candidate.child_target_id)]
        return StrategyService._pairs_have_cycle(graph)

    @staticmethod
    def _graph_has_cycle(relationships: tuple[TargetRelationship, ...]) -> bool:
        return StrategyService._pairs_have_cycle(
            [
                (r.parent_target_id, r.child_target_id)
                for r in relationships
                if r.relationship_type.value == "CASCADE"
            ]
        )

    @staticmethod
    def _pairs_have_cycle(edges: list[tuple[str, str]]) -> bool:
        graph: dict[str, set[str]] = {}
        for parent, child in edges:
            graph.setdefault(parent, set()).add(child)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            if any(visit(child) for child in graph.get(node, ())):
                return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(visit(node) for node in graph)

    @staticmethod
    def _hash(value: Any) -> str:
        raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(raw).hexdigest()

    async def _audit(
        self,
        event_type: str,
        entity_type: str,
        entity_id: str,
        principal: Principal,
        correlation_id: str,
        **metadata: Any,
    ) -> None:
        event = AuditEvent(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
            outcome="SUCCESS",
            occurred_at=datetime.now(UTC),
            reason="Authoritative strategy state changed",
            metadata=metadata,
        )
        events = self._pending_audit.get()
        if events is None:
            await self.audit.append(event)
        else:
            events.append(event)
