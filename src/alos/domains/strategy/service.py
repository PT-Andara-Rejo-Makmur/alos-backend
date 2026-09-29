"""Backend-authoritative planning lifecycle and cascade orchestration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from alos.audit import AuditEvent, AuditSink
from alos.domains.strategy.authority import authorize
from alos.domains.strategy.cascade import CascadeEngine
from alos.domains.strategy.constraints import activation_blockers, evaluate_constraint
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
    Plan,
    PlanningAssumption,
    Target,
    TargetRelationship,
    TargetRevision,
    VerificationState,
)
from alos.domains.strategy.repository import StrategyRepository
from alos.identity import Principal
from alos.security.errors import PlatformError


class StrategyService:
    def __init__(self, repository: StrategyRepository, audit: AuditSink) -> None:
        self.repository = repository
        self.audit = audit
        self.engine = CascadeEngine()

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
        return {"authorized_actions": actions}

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
        existing = await self.repository.get_plan(plan.plan_id)
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

    async def get_plan(self, principal: Principal, plan_id: str) -> Plan:
        plan = await self._plan(plan_id)
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

    async def create_objective(self, principal: Principal, objective: Objective) -> Objective:
        plan = await self._plan(objective.plan_id, objective.plan_version)
        self._require_draft(plan)
        await self._authorize_plan_mutation(principal, plan)
        self._scope_match(
            plan, objective.tenant_id, objective.organization_id, objective.owner_workspace_id
        )
        await self.repository.save_objective(objective)
        return objective

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

    async def create_observation(
        self, principal: Principal, observation: Observation
    ) -> Observation:
        target = await self._target(observation.target_id, observation.target_version)
        if target.lifecycle_state is not LifecycleState.DRAFT:
            raise PlatformError(
                "STRATEGY_IMMUTABLE",
                "Observations may only be added to a DRAFT target version.",
                status_code=409,
            )
        authorize(
            principal,
            "company_manage" if target.scope.type == "COMPANY" else "division_manage",
            tenant_id=target.tenant_id,
            organization_id=target.organization_id,
            owner_workspace_id=target.owner_workspace_id,
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

    async def get_target(self, principal: Principal, target_id: str) -> Target:
        target = await self._target(target_id)
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
        relationships = await self.repository.list_relationships(
            relationship.tenant_id, relationship.organization_id
        )
        if any(
            r.relationship_type == relationship.relationship_type
            and r.parent_target_id == relationship.parent_target_id
            and r.child_target_id == relationship.child_target_id
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
    ) -> CascadeRun:
        root = await self._target(root_target_id, root_target_version)
        authorize(principal, "read", tenant_id=root.tenant_id, organization_id=root.organization_id)
        available_assumptions = await self.repository.list_assumptions(
            root.tenant_id, root.organization_id
        )
        assumption_snapshot: dict[str, Any] = {}
        for assumption_id in assumption_refs:
            matching = [
                item for item in available_assumptions if item.assumption_id == assumption_id
            ]
            selected = max(matching, key=lambda item: item.version) if matching else None
            assumption_snapshot[assumption_id] = (
                None
                if selected is None
                or selected.value is None
                or selected.verification_state is not VerificationState.VERIFIED
                else asdict(selected)
            )
        assumptions_incomplete = any(value is None for value in assumption_snapshot.values())
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
                {"inputs": input_snapshot, "rules": tuple(asdict(rule) for rule in rules)}
            ),
            result_hash=self._hash(result_snapshot),
            created_by=principal.actor_id,
            created_at=datetime.now(UTC),
            status=status,
            tenant_id=root.tenant_id,
            organization_id=root.organization_id,
            owner_workspace_id=root.owner_workspace_id,
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

    async def accept_cascade(
        self, principal: Principal, run_id: str, derived_targets: tuple[Target, ...]
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
        if run.status is not CascadeStatus.VALID:
            raise PlatformError(
                "STRATEGY_CASCADE_NOT_ACCEPTABLE",
                "Only a VALID cascade may be accepted.",
                status_code=409,
            )
        root = await self._target(run.root_target_id, run.root_target_version)
        root_plan = await self._plan(root.plan_id, root.plan_version)
        self._require_draft(root_plan)
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
                await self.repository.save_observation(
                    Observation(
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
                        verified_at=run.created_at,
                        tenant_id=target.tenant_id,
                        organization_id=target.organization_id,
                        owner_workspace_id=target.owner_workspace_id,
                        correlation_id=run.correlation_id,
                    )
                )
        accepted = replace(run, status=CascadeStatus.ACCEPTED)
        await self.repository.save_cascade_run(accepted)
        await self._audit(
            "CASCADE_ACCEPTED", "cascade_run", run.cascade_run_id, principal, run.correlation_id
        )
        return accepted

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

    async def submit_plan(self, principal: Principal, plan_id: str) -> Plan:
        plan = await self._plan(plan_id)
        await self._authorize_plan_mutation(principal, plan)
        self._require_draft(plan)
        return await self._transition(
            plan, LifecycleState.UNDER_REVIEW, "PLAN_SUBMITTED", principal
        )

    async def approve_plan(self, principal: Principal, plan_id: str) -> Plan:
        plan = await self._plan(plan_id)
        authorize(
            principal, "approve", tenant_id=plan.tenant_id, organization_id=plan.organization_id
        )
        if plan.lifecycle_state is not LifecycleState.UNDER_REVIEW:
            raise PlatformError(
                "STRATEGY_LIFECYCLE_CONFLICT",
                "Only UNDER_REVIEW plan may be approved.",
                status_code=409,
            )
        return await self._transition(plan, LifecycleState.APPROVED, "PLAN_APPROVED", principal)

    async def activate_plan(
        self, principal: Principal, plan_id: str, constraints: tuple[Constraint, ...] = ()
    ) -> Plan:
        plan = await self._plan(plan_id)
        authorize(
            principal, "activate", tenant_id=plan.tenant_id, organization_id=plan.organization_id
        )
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
        for target in targets:
            if target.plan_id == plan.plan_id and target.plan_version == plan.version:
                observations = await self.repository.list_observations(
                    target.target_id, target.version
                )
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
                target_values = tuple(
                    item for item in observations if item.kind is ObservationKind.TARGET
                )
                if not target_values or any(
                    item.verification_state is not VerificationState.VERIFIED
                    or item.value is None
                    or item.unit != target.unit
                    or item.period != target.period
                    for item in target_values
                ):
                    raise PlatformError(
                        "STRATEGY_OBSERVATION_BLOCKED",
                        "Every target requires a VERIFIED TARGET observation before activation.",
                        status_code=409,
                        details={
                            "target_id": target.target_id,
                            "target_version": target.version,
                        },
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
                await self.repository.save_plan(
                    replace(
                        previous,
                        lifecycle_state=LifecycleState.SUPERSEDED,
                        updated_at=datetime.now(UTC),
                    )
                )
        return await self._transition(plan, LifecycleState.ACTIVE, "PLAN_ACTIVATED", principal)

    async def _transition(
        self, plan: Plan, state: LifecycleState, event: str, principal: Principal
    ) -> Plan:
        updated = plan.transition(state)
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
        await self.audit.append(
            AuditEvent(
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
        )
