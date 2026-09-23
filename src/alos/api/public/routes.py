import hashlib
from datetime import UTC, datetime
from typing import Any, NoReturn

from fastapi import APIRouter, Request, Response

from alos import __version__
from alos.agents.lifecycle import RunAuthorityError
from alos.api.models import ResearchRequestBody, SystemInfoResponse
from alos.audit import AuditEvent
from alos.authentication.models import (
    AccountAccessProjection,
    AccountStateProjection,
    ActiveWorkspaceProjection,
    ActiveWorkspaceRequest,
    AuthenticatedPrincipalProjection,
    AuthTokenResponse,
    LoginRequest,
    MembershipMutationRequest,
    ProvisionAccountRequest,
    RegisterRequest,
    WorkspaceAccessProjection,
)
from alos.authorization import AuthorizationEnforcer
from alos.context import build_context_projection
from alos.contracts import ContractValidationError
from alos.dependencies import (
    AuthorizationEnforcerDependency,
    CapabilityRegistryDependency,
    ContractCatalogDependency,
    CurrentPrincipalDependency,
    FactoryOrchestratorDependency,
    GenesisClientDependency,
    IntegrationContractValidatorDependency,
    ResearchServiceDependency,
    RuntimeOrchestratorDependency,
    SkillServiceDependency,
)
from alos.evidence import resolve_registry_result
from alos.identity import Principal
from alos.integrations.genesis import GenesisClientError, IntegrationContractError
from alos.observability.correlation import current_correlation_id
from alos.persistence.models import (
    BacklogCandidateRecord,
    ResearchFindingRecord,
    ResearchRecommendationRecord,
    ReviewPackageRecord,
)
from alos.registry import DecisionAuthority, RegistryConflictError
from alos.registry_contracts import RegistryAuthorityView, RegistryAuthorizationError
from alos.research import ResearchCommand, project_domain_access
from alos.security.errors import PlatformError
from alos.skills.assignment import SkillAssignmentError
from alos.skills.models import SkillAssignmentRequest

router = APIRouter(prefix="/api/v1", tags=["system"])

CONTEXT_PROJECTION_SCHEMA = "https://schemas.alos.dev/v1/context/context-projection.schema.json"
DOMAIN_ACCESS_SCHEMA = "https://schemas.alos.dev/v1/research/domain-access-response.schema.json"
SKILL_LIST_SCHEMA = "https://schemas.alos.dev/v1/skill/skill-list-response.schema.json"
SKILL_DETAIL_SCHEMA = "https://schemas.alos.dev/v1/skill/skill-detail.schema.json"
REVIEW_PACKAGE_SCHEMA = "https://schemas.alos.dev/v1/review/review-package.schema.json"
REVIEW_INVOCATION_SCHEMA = "https://schemas.alos.dev/v1/review/review-invocation.schema.json"
RESEARCH_REQUEST_SCHEMA = "https://schemas.alos.dev/v1/research/research-request.schema.json"
RESEARCH_RESULT_SCHEMA = "https://schemas.alos.dev/v1/research/research-result.schema.json"


@router.post("/integration/bootstrap", tags=["integration"], include_in_schema=False)
async def bootstrap_deterministic_integration(
    request: Request,
    principal: CurrentPrincipalDependency,
) -> dict[str, Any]:
    """Create one fixed diagnostic definition only in an explicit non-production test stack."""

    settings = request.app.state.settings
    if not settings.ENABLE_TEST_TOOLS or settings.APP_ENV not in {"development", "test"}:
        raise PlatformError(
            "INTEGRATION_BOOTSTRAP_DENIED",
            "Deterministic integration bootstrap is disabled.",
            status_code=403,
        )
    registry = request.app.state.agent_registry
    if registry is None:
        raise PlatformError(
            "AGENT_REGISTRY_UNAVAILABLE", "Agent registry is unavailable.", status_code=503
        )
    agent_id = "agent.runtime.diagnostic"
    version = "1.0.0"
    correlation_id = current_correlation_id()
    try:
        entry = await registry.register(
            {
                "tenant_id": principal.tenant_id,
                "organization_id": principal.organization_id,
                "workspace_id": principal.workspace_id,
                "correlation_id": correlation_id,
                "agent_id": agent_id,
                "agent_version": version,
                "owner_actor_id": principal.actor_id,
                "name": "Runtime Diagnostic",
                "purpose": "Validate the governed Backend and GENESIS runtime boundary.",
                "risk_level": "LOW",
                "capability_ids": ["capability.runtime.diagnostic"],
                "skill_refs": [],
                "model_policy_ref": "policy.runtime-test",
                "tool_ids": ["diagnostic.echo"],
                "permission_refs": ["tools.diagnostic.execute"],
                "scope_refs": ["scope.diagnostic"],
                "input_schema": {"type": "object"},
                "output_schema": {
                    "type": "object",
                    "required": ["summary", "tool_status"],
                    "additionalProperties": True,
                },
                "approval_required": False,
                "execution_budget": {"max_tokens": 100, "max_steps": 3},
                "delegation_policy": {"enabled": False, "max_depth": 0},
            },
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
        )
        entry = await registry.approve(
            tenant_id=entry.tenant_id,
            workspace_id=entry.workspace_id,
            subject_id=entry.subject_id,
            version=entry.version,
            actor_id="integration_it_authority",
            decision_id=f"decision.{agent_id}.{version}",
            authority=DecisionAuthority.IT,
            correlation_id=correlation_id,
        )
        entry = await registry.activate(
            tenant_id=entry.tenant_id,
            workspace_id=entry.workspace_id,
            subject_id=entry.subject_id,
            version=entry.version,
            actor_id="integration_release_authority",
            release_id=f"release.{agent_id}.{version}",
            correlation_id=correlation_id,
        )
    except RegistryConflictError:
        entry = registry.get(
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            subject_id=agent_id,
            version=version,
        )
    evidence_registry = request.app.state.evidence_registry
    if evidence_registry is None:
        raise PlatformError(
            "EVIDENCE_REGISTRY_UNAVAILABLE",
            "Evidence registry is unavailable.",
            status_code=503,
        )
    evidence_ref = await resolve_registry_result(
        evidence_registry.register(
            {
                "tenant_id": principal.tenant_id,
                "organization_id": principal.organization_id,
                "workspace_id": principal.workspace_id,
                "correlation_id": correlation_id,
                "scope_refs": ["scope.diagnostic"],
                "evidence_id": "evidence.integration."
                + hashlib.sha256(
                    (f"{principal.tenant_id}:{principal.workspace_id}:{correlation_id}").encode()
                ).hexdigest()[:24],
                "source_id": "source.integration.review",
                "uri": f"urn:alos:integration:evidence:{correlation_id}",
                "captured_at": "2026-09-23T10:00:00Z",
                "content_hash": "sha256:" + "b" * 64,
                "source_version": "1.0.0",
                "anchor": "integration-fixture",
                "excerpt": "Deterministic Backend-authorized integration evidence.",
                "data_classification": "INTERNAL",
                "instruction_authority": False,
                "validation_status": "VALID",
                "metadata": {
                    "review_subject": {
                        "subject_id": entry.subject_id,
                        "subject_version": entry.version,
                    }
                },
            }
        )
    )
    return {
        "agent_id": entry.subject_id,
        "agent_version": entry.version,
        "registry_digest": entry.digest,
        "state": entry.state.value,
        "evidence_ref": evidence_ref,
    }


@router.post("/integration/research", tags=["integration"], include_in_schema=False)
async def execute_deterministic_integration_research(
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    genesis: GenesisClientDependency,
) -> dict[str, Any]:
    """Run and persist canonical deterministic research only in the integration stack."""

    settings = request.app.state.settings
    if not settings.ENABLE_TEST_TOOLS or settings.APP_ENV not in {"development", "test"}:
        raise PlatformError(
            "INTEGRATION_RESEARCH_DENIED",
            "Deterministic integration research is disabled.",
            status_code=403,
        )
    evidence_registry = request.app.state.evidence_registry
    if evidence_registry is None:
        raise PlatformError(
            "EVIDENCE_REGISTRY_UNAVAILABLE", "Evidence registry is unavailable.", status_code=503
        )
    evidence_id = str(payload.get("evidence_id", ""))
    try:
        evidence = await resolve_registry_result(evidence_registry.get(evidence_id))
    except LookupError as exc:
        raise PlatformError(
            "RESEARCH_EVIDENCE_NOT_AUTHORIZED",
            "Research evidence is not registered by Backend authority.",
            status_code=403,
        ) from exc
    if (
        evidence.get("tenant_id") != principal.tenant_id
        or evidence.get("organization_id") != principal.organization_id
        or evidence.get("workspace_id") != principal.workspace_id
        or evidence.get("validation_status") != "VALID"
        or not set(evidence.get("scope_refs", [])).issubset(principal.scopes)
    ):
        raise PlatformError(
            "RESEARCH_EVIDENCE_NOT_AUTHORIZED",
            "Research evidence is outside Backend-authorized identity or scope.",
            status_code=403,
        )
    correlation_id = current_correlation_id()
    suffix = hashlib.sha256(
        f"{principal.tenant_id}:{correlation_id}:{evidence_id}".encode()
    ).hexdigest()[:20]
    context = {
        "context_id": f"context.integration.{suffix}",
        "tenant_id": principal.tenant_id,
        "organization_id": principal.organization_id,
        "workspace_id": principal.workspace_id,
        "actor_id": principal.actor_id,
        "correlation_id": correlation_id,
        "scope_refs": list(evidence["scope_refs"]),
        "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "items": [
            {
                "key": "integration-evidence",
                "value": evidence.get("excerpt", "Authorized integration evidence."),
                "source_id": evidence["source_id"],
                "evidence_id": evidence["evidence_id"],
                "source_version": evidence.get("source_version", "1.0.0"),
                "content_hash": evidence["content_hash"],
                "anchor": evidence.get("anchor", "integration-fixture"),
                "data_classification": evidence.get("data_classification", "INTERNAL"),
                "instruction_authority": False,
            }
        ],
        "evidence_refs": [evidence],
    }
    invocation = contracts.validate(
        RESEARCH_REQUEST_SCHEMA,
        {
            "research_id": f"research.integration.{suffix}",
            "run_id": f"run.integration.research.{suffix}",
            "correlation_id": correlation_id,
            "domain": "TECHNOLOGY",
            "question": str(payload.get("question") or "Analyze authorized evidence."),
            "execution_context": {
                "tenant_id": principal.tenant_id,
                "organization_id": principal.organization_id,
                "workspace_id": principal.workspace_id,
                "actor_id": principal.actor_id,
                "authority_context": {
                    "role": next(iter(sorted(principal.roles)), "DIVISION_MEMBER"),
                    "role_refs": sorted(principal.roles),
                    "authority_level": "REQUESTER",
                },
                "permission_refs": sorted(principal.permissions),
                "allowed_tool_ids": [],
                "scope_refs": list(evidence["scope_refs"]),
                "data_classification": evidence.get("data_classification", "INTERNAL"),
                "correlation_id": correlation_id,
                "execution_budget": {"max_tokens": 200, "max_steps": 2},
            },
            "scope": list(evidence["scope_refs"]),
            "context_bundle": context,
        },
    )
    try:
        result = await genesis.execute_research(invocation, correlation_id=correlation_id)
        result = contracts.validate(RESEARCH_RESULT_SCHEMA, result)
    except GenesisClientError as exc:
        raise PlatformError(
            exc.code,
            exc.message,
            status_code=exc.status_code or 502,
            retryable=exc.retryable,
            correlation_id=exc.correlation_id,
        ) from exc
    if result["correlation_id"] != correlation_id:
        raise PlatformError(
            "GENESIS_RESEARCH_IDENTITY_MISMATCH",
            "GENESIS research result changed the Backend-issued correlation identity.",
            status_code=502,
        )
    await _persist_integration_research_result(request, principal, result)
    return result


@router.post("/agent-runs", tags=["runtime"])
async def execute_agent_run(
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    runtime: RuntimeOrchestratorDependency,
) -> dict[str, Any]:
    registry = request.app.state.agent_registry
    if registry is None:
        raise PlatformError(
            "AGENT_REGISTRY_UNAVAILABLE", "Agent registry is unavailable.", status_code=503
        )
    try:
        agent = registry.get(
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            subject_id=str(payload.get("agent_id", "")),
            version=str(payload.get("agent_version", "")),
        )
        record = await runtime.execute(
            payload,
            principal=principal,
            agent=agent,
            test_mode_allowed=(
                request.app.state.settings.ENABLE_TEST_TOOLS
                and request.app.state.settings.APP_ENV in {"development", "test"}
            ),
        )
        return dict(record.result or {})
    except GenesisClientError as exc:
        raise PlatformError(
            exc.code,
            exc.message,
            status_code=exc.status_code or 502,
            retryable=exc.retryable,
            correlation_id=exc.correlation_id,
        ) from exc
    except (LookupError, RegistryAuthorizationError, RunAuthorityError, ValueError) as exc:
        raise PlatformError("RUN_NOT_AUTHORIZED", str(exc), status_code=403) from exc


@router.post("/agent-runs/{run_id}/cancellation", tags=["runtime"])
async def cancel_agent_run(
    run_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
) -> dict[str, Any]:
    authority = request.app.state.agent_run_authority
    current = await authority.get(run_id)
    if (
        current.tenant_id != principal.tenant_id
        or current.organization_id != principal.organization_id
        or current.workspace_id != principal.workspace_id
    ):
        raise PlatformError("RUN_NOT_AUTHORIZED", "Run is outside caller scope.", status_code=403)
    cancelled = await authority.request_cancel(
        run_id,
        actor_id=principal.actor_id,
        correlation_id=current_correlation_id(),
    )
    return {"run_id": cancelled.run_id, "status": cancelled.status.value}


@router.post("/reviews", tags=["review"])
async def request_advisory_review(
    payload: dict[str, Any],
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    genesis: GenesisClientDependency,
) -> dict[str, Any]:
    try:
        payload = contracts.validate(REVIEW_INVOCATION_SCHEMA, payload)
    except ContractValidationError as exc:
        raise PlatformError(
            "REVIEW_REQUEST_INVALID",
            "Review request does not satisfy the canonical contract.",
            status_code=422,
        ) from exc
    subject = payload.get("subject")
    evaluation_subject = payload.get("evaluation_subject")
    if not isinstance(subject, dict) or not isinstance(evaluation_subject, dict):
        raise PlatformError(
            "REVIEW_REQUEST_INVALID", "Review snapshots are required.", status_code=422
        )
    identity = {
        "tenant_id": principal.tenant_id,
        "organization_id": principal.organization_id,
        "workspace_id": principal.workspace_id,
        "correlation_id": current_correlation_id(),
    }
    if any(subject.get(key) != value for key, value in identity.items()) or any(
        evaluation_subject.get(key) != value for key, value in identity.items()
    ):
        raise PlatformError(
            "REVIEW_SCOPE_DENIED",
            "Review snapshot is outside the Backend-authorized identity or correlation scope.",
            status_code=403,
        )
    registry = request.app.state.agent_registry
    evidence_registry = request.app.state.evidence_registry
    if registry is None or evidence_registry is None:
        raise PlatformError(
            "REVIEW_AUTHORITY_UNAVAILABLE",
            "Authoritative review registries are unavailable.",
            status_code=503,
        )
    try:
        exact = registry.get(
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            subject_id=str(subject.get("subject_id", "")),
            version=str(subject.get("subject_version", "")),
        )
        authority = RegistryAuthorityView.from_entry(exact).authorize(principal)
        if authority.lifecycle not in {"DRAFT", "APPROVED", "ACTIVE"}:
            raise RegistryAuthorizationError("agent lifecycle is not eligible for advisory review")
        authoritative_evidence = {
            str(item.get("evidence_id", "")): await resolve_registry_result(
                evidence_registry.get(str(item.get("evidence_id", "")))
            )
            for item in subject.get("evidence_refs", [])
            if isinstance(item, dict)
        }
        _require_authoritative_review_snapshot(
            subject=subject,
            evaluation_subject=evaluation_subject,
            agent_payload=exact.payload,
            authority=authority,
            authoritative_evidence=authoritative_evidence,
            skill_registry=request.app.state.skill_registry,
        )
    except (LookupError, RegistryAuthorizationError) as exc:
        raise PlatformError("REVIEW_NOT_AUTHORIZED", str(exc), status_code=403) from exc
    try:
        package = await genesis.review(payload, correlation_id=current_correlation_id())
    except GenesisClientError as exc:
        raise PlatformError(
            exc.code,
            exc.message,
            status_code=exc.status_code or 502,
            retryable=exc.retryable,
            correlation_id=exc.correlation_id,
        ) from exc
    try:
        package = contracts.validate(REVIEW_PACKAGE_SCHEMA, package)
        _require_review_package_matches_invocation(package=package, invocation=payload)
    except ContractValidationError as exc:
        raise PlatformError(
            "GENESIS_REVIEW_RESPONSE_INVALID",
            "GENESIS returned a response that violates the canonical ReviewPackage contract.",
            status_code=502,
        ) from exc
    if "it_decision" in package or "director_decision" in package:
        raise PlatformError(
            "GENESIS_AUTHORITY_VIOLATION",
            "GENESIS review response cannot contain authoritative decisions.",
            status_code=502,
        )
    package_identity = package["identity"]
    review_id = str(package_identity["review_id"])
    async with request.app.state.database.session_factory() as session:
        existing = await session.get(ReviewPackageRecord, review_id)
        if existing is not None:
            raise PlatformError(
                "REVIEW_ID_CONFLICT",
                "Review ID is already bound to an immutable advisory package.",
                status_code=409,
            )
        if existing is None:
            contract_version = "unknown"
            contracts_path = request.app.state.settings.ALOS_CONTRACTS_PATH
            if contracts_path is not None and (contracts_path / "VERSION").is_file():
                contract_version = (contracts_path / "VERSION").read_text(encoding="utf-8").strip()
            session.add(
                ReviewPackageRecord(
                    review_id=review_id,
                    tenant_id=principal.tenant_id,
                    workspace_id=principal.workspace_id,
                    subject_id=str(package_identity["subject_id"]),
                    subject_version=str(package_identity["subject_version"]),
                    contract_version=contract_version,
                    evidence_uri=f"urn:alos:review-package:{review_id}",
                    recorded_at=datetime.now(UTC),
                )
            )
            await session.commit()
    return package


def _require_authoritative_review_snapshot(
    *,
    subject: dict[str, Any],
    evaluation_subject: dict[str, Any],
    agent_payload: dict[str, Any],
    authority: RegistryAuthorityView,
    authoritative_evidence: dict[str, dict[str, Any]],
    skill_registry: Any,
) -> None:
    """Reject review facts that were not issued or verified by Backend authority."""

    expected_materiality = "MATERIAL" if authority.risk in {"HIGH", "CRITICAL"} else "NON_MATERIAL"
    if subject.get("materiality") != expected_materiality:
        _review_authority_error("materiality does not match the authoritative risk level")
    if subject.get("purpose") != agent_payload.get("purpose"):
        _review_authority_error("purpose does not match the authoritative agent definition")
    if subject.get("business_context") != {
        "registry_digest": authority.digest,
        "subject_type": "agent",
    }:
        _review_authority_error("business context was not issued by Backend authority")
    if subject.get("execution_budget") != agent_payload.get("execution_budget"):
        _review_authority_error(
            "execution budget does not match the authoritative agent definition"
        )
    if set(subject.get("scope", [])) != set(authority.scope):
        _review_authority_error("scope does not match the authoritative agent definition")
    if set(subject.get("permissions", [])) != set(authority.permissions):
        _review_authority_error("permissions do not match the authoritative agent definition")

    capability = subject.get("capability")
    if not isinstance(capability, dict):
        _review_authority_error("capability snapshot is invalid")
    capability_ids = set(agent_payload.get("capability_ids", []))
    expected_capability = {
        "capability_id": capability.get("capability_id"),
        "version": authority.version,
        "name": agent_payload.get("name"),
        "purpose": agent_payload.get("purpose"),
        "owner": authority.owner,
        "capability_type": "AGENT",
        "output_state": "NEEDS_REVIEW",
        "lifecycle_state": "DRAFT",
        "scope_refs": list(authority.scope),
        "tool_ids": list(authority.tools),
        "permission_refs": list(authority.permissions),
        "risk_level": authority.risk,
    }
    if capability.get("capability_id") not in capability_ids or capability != expected_capability:
        _review_authority_error("capability does not match the authoritative agent definition")

    tools = subject.get("tools", [])
    if {str(item.get("tool_id")) for item in tools if isinstance(item, dict)} != set(
        authority.tools
    ):
        _review_authority_error("tools do not match the authoritative agent definition")
    expected_tools = [
        {
            "tool_id": tool_id,
            "purpose": f"Backend-authorized tool: {tool_id}",
            "backend_executor": True,
        }
        for tool_id in authority.tools
    ]
    if tools != expected_tools:
        _review_authority_error("tool snapshots were not issued by Backend authority")

    model_policy = subject.get("model_policy")
    if not isinstance(model_policy, dict) or model_policy != {
        "gateway_required": True,
        "policy_ref": agent_payload.get("model_policy_ref"),
    }:
        _review_authority_error("model policy does not match the authoritative agent definition")
    delegation = agent_payload.get("delegation_policy", {})
    if subject.get("delegation_policy") != {
        "enabled": delegation.get("enabled"),
        "lineage_required": True,
        "max_depth": delegation.get("max_depth"),
    }:
        _review_authority_error(
            "delegation policy does not match the authoritative agent definition"
        )

    skill_refs = {
        (str(item.get("skill_id")), str(item.get("skill_version")))
        for item in agent_payload.get("skill_refs", [])
        if isinstance(item, dict)
    }
    skills = subject.get("skills", [])
    supplied_skill_refs = {
        (str(item.get("skill_id")), str(item.get("skill_version")))
        for item in skills
        if isinstance(item, dict)
    }
    if supplied_skill_refs != skill_refs or len(skills) != len(skill_refs):
        _review_authority_error("skills do not match the authoritative exact-version references")
    for skill in skills:
        if skill_registry is None:
            _review_authority_error("authoritative skill registry is unavailable")
        exact_skill = skill_registry.get(
            tenant_id=authority.tenant_id,
            workspace_id=authority.workspace_id,
            subject_id=str(skill["skill_id"]),
            version=str(skill["skill_version"]),
        )
        if exact_skill.payload != skill:
            _review_authority_error("skill definition does not match the authoritative registry")

    observations = evaluation_subject.get("observations")
    if observations:
        _review_authority_error("caller-supplied assurance observations are not authoritative")
    for evidence in subject.get("evidence_refs", []):
        registered = authoritative_evidence.get(str(evidence.get("evidence_id", "")))
        if registered != evidence:
            raise PlatformError(
                "REVIEW_EVIDENCE_NOT_AUTHORITATIVE",
                "Review evidence does not match the Backend-authorized reference.",
                status_code=403,
            )
        evidence_metadata = evidence.get("metadata", {})
        expected_subject = {
            "subject_id": authority.subject_id,
            "subject_version": authority.version,
        }
        if (
            evidence.get("tenant_id") not in {None, authority.tenant_id}
            or evidence.get("organization_id") not in {None, authority.organization_id}
            or evidence.get("workspace_id") not in {None, authority.workspace_id}
            or not set(evidence.get("scope_refs", [])).issubset(authority.scope)
            or evidence.get("validation_status") != "VALID"
            or not isinstance(evidence_metadata, dict)
            or evidence_metadata.get("review_subject") != expected_subject
        ):
            raise PlatformError(
                "REVIEW_EVIDENCE_NOT_AUTHORITATIVE",
                "Review evidence is outside the Backend-authorized identity or scope.",
                status_code=403,
            )


def _require_review_package_matches_invocation(
    *, package: dict[str, Any], invocation: dict[str, Any]
) -> None:
    subject = invocation["subject"]
    identity = package["identity"]
    expected_identity = {
        "review_id": subject["review_id"],
        "tenant_id": subject["tenant_id"],
        **({"organization_id": subject["organization_id"]} if "organization_id" in subject else {}),
        "workspace_id": subject["workspace_id"],
        "correlation_id": subject["correlation_id"],
        "subject_id": subject["subject_id"],
        "subject_version": subject["subject_version"],
    }
    if identity != expected_identity:
        raise PlatformError(
            "GENESIS_REVIEW_IDENTITY_MISMATCH",
            "GENESIS review identity does not match the Backend-issued invocation.",
            status_code=502,
        )
    for key in (
        "purpose",
        "materiality",
        "business_context",
        "capability",
        "scope",
        "permissions",
        "skills",
        "tools",
        "model_policy",
        "delegation_policy",
        "execution_budget",
        "evidence_refs",
    ):
        if package.get(key) != subject.get(key):
            raise PlatformError(
                "GENESIS_REVIEW_SNAPSHOT_MISMATCH",
                "GENESIS review package altered the Backend-issued subject snapshot.",
                status_code=502,
            )


def _review_authority_error(reason: str) -> NoReturn:
    raise PlatformError(
        "REVIEW_FACTS_NOT_AUTHORITATIVE",
        f"Review snapshot rejected: {reason}.",
        status_code=403,
    )


async def _persist_integration_research_result(
    request: Request, principal: Any, result: dict[str, Any]
) -> None:
    """Persist canonical research output as Backend-owned records and draft backlog only."""

    async with request.app.state.database.session_factory() as session:
        for finding in result["findings"]:
            finding_id = str(finding["finding_id"])
            if await session.get(ResearchFindingRecord, finding_id) is None:
                session.add(
                    ResearchFindingRecord(
                        finding_id=finding_id,
                        tenant_id=principal.tenant_id,
                        organization_id=principal.organization_id,
                        workspace_id=principal.workspace_id,
                        actor_id=principal.actor_id,
                        correlation_id=str(result["correlation_id"]),
                        kind=str(finding.get("finding_type", "RESEARCH")),
                        domain=str(finding.get("domain", "TECHNOLOGY")),
                        statement=str(finding["statement"]),
                        evidence_refs=[
                            str(item["evidence_id"]) for item in finding.get("evidence_refs", [])
                        ],
                        confidence=float(finding["confidence"]),
                        source_ref=None,
                        retrieval_metadata={
                            "research_id": result["research_id"],
                            "run_id": result["run_id"],
                            "output_state": result["output_state"],
                        },
                        created_at=datetime.now(UTC),
                    )
                )
        for recommendation in result["recommendations"]:
            recommendation_id = str(recommendation["recommendation_id"])
            finding_ids = [str(item) for item in recommendation.get("finding_ids", [])]
            finding_id = finding_ids[0] if finding_ids else "finding.unbound"
            evidence_ids = [
                str(item["evidence_id"]) for item in recommendation.get("evidence_refs", [])
            ]
            if await session.get(ResearchRecommendationRecord, recommendation_id) is None:
                session.add(
                    ResearchRecommendationRecord(
                        recommendation_id=recommendation_id,
                        finding_id=finding_id,
                        tenant_id=principal.tenant_id,
                        organization_id=principal.organization_id,
                        workspace_id=principal.workspace_id,
                        actor_id=principal.actor_id,
                        correlation_id=str(result["correlation_id"]),
                        recommendation=str(recommendation["summary"]),
                        impact=str(recommendation["recommended_action"]),
                        priority_suggestion="REVIEW",
                        owner_suggestion=None,
                        evidence_refs=evidence_ids,
                        domain="TECHNOLOGY",
                        created_at=datetime.now(UTC),
                    )
                )
            candidate_id = f"candidate.{recommendation_id}"
            if (
                recommendation.get("backlog_candidate") is True
                and await session.get(BacklogCandidateRecord, candidate_id) is None
            ):
                session.add(
                    BacklogCandidateRecord(
                        candidate_id=candidate_id,
                        finding_id=finding_id,
                        recommendation_id=recommendation_id,
                        impact=str(recommendation["recommended_action"]),
                        priority_suggestion="REVIEW",
                        owner_suggestion=None,
                        evidence_refs=evidence_ids,
                        approval_state="DRAFT",
                        actor_id=principal.actor_id,
                        scope_ref="scope.diagnostic",
                        correlation_id=str(result["correlation_id"]),
                        created_at=datetime.now(UTC),
                    )
                )
        await session.commit()


@router.get("/genesis/context-options", tags=["context"])
async def get_context_projection(
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    projection = build_context_projection(
        principal,
        correlation_id=current_correlation_id(),
    )
    return contracts.validate(CONTEXT_PROJECTION_SCHEMA, projection)


@router.get("/research/domain-access", tags=["research"])
async def get_research_domain_access(
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    projection = project_domain_access(
        principal,
        correlation_id=current_correlation_id(),
    )
    return contracts.validate(DOMAIN_ACCESS_SCHEMA, projection)


@router.get("/skills", tags=["skills"])
async def list_skills(
    principal: CurrentPrincipalDependency,
    skills: SkillServiceDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    result = {
        "skills": skills.list_skills(principal=principal),
        "correlation_id": current_correlation_id(),
    }
    return contracts.validate(SKILL_LIST_SCHEMA, result)


@router.get("/skills/{skill_id}", tags=["skills"])
async def get_skill_detail(
    skill_id: str,
    principal: CurrentPrincipalDependency,
    skills: SkillServiceDependency,
    contracts: ContractCatalogDependency,
) -> dict[str, Any]:
    result = skills.get_skill(principal=principal, skill_id=skill_id)
    return contracts.validate(SKILL_DETAIL_SCHEMA, result)


@router.get("/skills/{skill_id}/versions", tags=["skills"])
async def get_skill_versions(
    skill_id: str,
    principal: CurrentPrincipalDependency,
    skills: SkillServiceDependency,
) -> dict[str, Any]:
    return {
        "skill_id": skill_id,
        "versions": skills.get_versions(
            principal=principal,
            skill_id=skill_id,
        ),
        "correlation_id": current_correlation_id(),
    }


@router.get("/agents/{agent_id}/skills", tags=["skills"])
async def list_agent_skills(
    agent_id: str,
    principal: CurrentPrincipalDependency,
    skills: SkillServiceDependency,
) -> dict[str, Any]:
    try:
        return skills.list_agent_skills(
            principal=principal,
            agent_id=agent_id,
            correlation_id=current_correlation_id(),
        )
    except (LookupError, RegistryAuthorizationError) as exc:
        raise PlatformError(
            "AGENT_NOT_FOUND",
            "The requested AgentDefinition is not available.",
            status_code=404,
            correlation_id=current_correlation_id(),
        ) from exc


@router.post("/agents/{agent_id}/skills", tags=["skills"], status_code=201)
async def assign_skill(
    agent_id: str,
    payload: SkillAssignmentRequest,
    principal: CurrentPrincipalDependency,
    skills: SkillServiceDependency,
) -> dict[str, Any]:
    if payload.agent_id != agent_id:
        raise PlatformError(
            "AGENT_ID_MISMATCH", "Path and payload agent_id must match.", status_code=400
        )
    try:
        response = await skills.assign_skill(
            request=payload,
            principal=principal,
            correlation_id=current_correlation_id(),
        )
    except SkillAssignmentError as exc:
        if exc.code in {"AGENT_VERSION_NOT_FOUND", "SKILL_VERSION_NOT_FOUND"}:
            status_code = 404
        elif exc.code in {"AGENT_SKILL_DUPLICATE", "AGENT_VERSION_CONFLICT"}:
            status_code = 409
        else:
            status_code = 403
        raise PlatformError(
            exc.code,
            exc.message,
            status_code=status_code,
            correlation_id=current_correlation_id(),
        ) from exc
    return response.model_dump()


@router.post("/research/requests", tags=["research"])
async def request_research(
    payload: ResearchRequestBody,
    principal: CurrentPrincipalDependency,
    research: ResearchServiceDependency,
) -> dict[str, Any]:
    return await research.request(
        ResearchCommand(
            question=payload.question,
            source_mode=payload.source_mode,
            domain=payload.domain,
        ),
        principal=principal,
        correlation_id=current_correlation_id(),
    )


@router.get("/capabilities/{capability_id}", tags=["capabilities"])
async def get_capability_detail(
    capability_id: str,
    principal: CurrentPrincipalDependency,
    capabilities: CapabilityRegistryDependency,
) -> dict[str, Any]:
    """Return the authoritative, authorized CapabilityDetail projection.

    A DRAFT is visible only to its creator; other consumers only receive
    authorized ACTIVE versions. Every other lifecycle state fails closed.
    """

    try:
        return capabilities.detail(
            tenant_id=principal.tenant_id,
            workspace_id=principal.workspace_id,
            capability_id=capability_id,
            principal=principal,
        )
    except LookupError as exc:
        raise PlatformError(
            "CAPABILITY_NOT_FOUND",
            "The requested capability version is not available to this principal.",
            status_code=404,
            correlation_id=current_correlation_id(),
        ) from exc
    except RegistryAuthorizationError as exc:
        raise PlatformError(
            "CAPABILITY_NOT_AUTHORIZED",
            "The principal lacks the authority required by this capability version.",
            status_code=403,
            correlation_id=current_correlation_id(),
        ) from exc
    except ValueError as exc:
        raise PlatformError(
            "CAPABILITY_DETAIL_INVALID",
            "Capability detail projection failed contract validation.",
            status_code=500,
            correlation_id=current_correlation_id(),
        ) from exc


@router.post("/genesis/factory/analyze", tags=["factory"])
async def analyze_factory_requirement(
    payload: dict[str, Any],
    principal: CurrentPrincipalDependency,
    orchestrator: FactoryOrchestratorDependency,
) -> dict[str, Any]:
    """Resolve a requirement through Backend authority and GENESIS intelligence."""

    return await orchestrator.analyze(
        payload,
        principal=principal,
        correlation_id=current_correlation_id(),
    )


@router.post("/auth/register", status_code=201, include_in_schema=False)
async def register(request: Request, payload: RegisterRequest) -> dict[str, Any]:
    settings = request.app.state.settings
    if settings.APP_ENV != "test" and not (
        settings.APP_ENV == "development" and settings.ENABLE_TEST_REGISTRATION
    ):
        raise PlatformError(
            "REGISTRATION_DISABLED",
            "public self-registration is disabled",
            status_code=404,
        )
    service = request.app.state.auth_service
    result: dict[str, Any] = await service.register_for_test(payload.model_dump())
    return result


@router.post("/identity/accounts", status_code=201, tags=["identity"])
async def provision_account(
    request: Request,
    payload: ProvisionAccountRequest,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> dict[str, Any]:
    correlation_id = current_correlation_id()
    decision = await authorization.enforce(
        principal=principal,
        required_permission="identity.accounts.manage",
        correlation_id=correlation_id,
        command="identity.account.provision",
    )
    if not decision.is_allowed:
        raise PlatformError(
            "AUTHORIZATION_DENIED",
            "identity.accounts.manage permission is required",
            status_code=403,
        )
    if (
        payload.tenant_id != principal.tenant_id
        or payload.organization_id != principal.organization_id
    ):
        raise PlatformError(
            "AUTHORITY_BOUNDARY_CONFLICT",
            "tenant and organization must match the authenticated authority boundary",
            status_code=403,
        )
    canonical_payload = payload.model_dump()
    canonical_payload.update(
        tenant_id=principal.tenant_id,
        organization_id=principal.organization_id,
    )
    result: dict[str, Any] = await request.app.state.auth_service.provision(canonical_payload)
    await request.app.state.identity_audit.append(
        AuditEvent(
            event_type="identity.account.provisioned",
            entity_type="actor",
            entity_id=str(result["actor"]["actor_id"]),
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=payload.workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
            outcome="SUCCEEDED",
            occurred_at=datetime.now(UTC),
            reason="Governed identity account provisioned",
            metadata={"email": payload.email, "role_refs": list(payload.role_refs)},
        )
    )
    return result


@router.get(
    "/identity/actors/{actor_id}/access",
    response_model=AccountAccessProjection,
    tags=["identity"],
)
async def list_account_access(
    request: Request,
    actor_id: str,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> AccountAccessProjection:
    await _require_identity_permission(
        authorization, principal, "identity.memberships.read", "identity.access.read"
    )
    result = await request.app.state.auth_service.list_account_access(
        actor_id,
        tenant_id=principal.tenant_id,
        organization_id=principal.organization_id,
    )
    return AccountAccessProjection.model_validate(result)


@router.post(
    "/identity/actors/{actor_id}/memberships",
    response_model=WorkspaceAccessProjection,
    status_code=201,
    tags=["identity"],
)
async def assign_workspace_membership(
    request: Request,
    actor_id: str,
    payload: MembershipMutationRequest,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> WorkspaceAccessProjection:
    correlation_id = await _require_identity_permission(
        authorization,
        principal,
        "identity.memberships.manage",
        "identity.membership.assign",
    )
    result = await request.app.state.auth_service.assign_membership(
        actor_id,
        payload.model_dump(),
        tenant_id=principal.tenant_id,
        organization_id=principal.organization_id,
    )
    await _record_identity_change(
        request,
        principal,
        correlation_id,
        event_type="identity.membership.assigned",
        actor_id=actor_id,
        workspace_id=payload.workspace_id,
    )
    return WorkspaceAccessProjection.model_validate(result)


@router.put(
    "/identity/actors/{actor_id}/memberships",
    response_model=WorkspaceAccessProjection,
    tags=["identity"],
)
async def update_workspace_membership(
    request: Request,
    actor_id: str,
    payload: MembershipMutationRequest,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> WorkspaceAccessProjection:
    correlation_id = await _require_identity_permission(
        authorization,
        principal,
        "identity.memberships.manage",
        "identity.membership.update",
    )
    result = await request.app.state.auth_service.update_membership(
        actor_id,
        payload.model_dump(),
        tenant_id=principal.tenant_id,
        organization_id=principal.organization_id,
    )
    await _record_identity_change(
        request,
        principal,
        correlation_id,
        event_type="identity.membership.updated",
        actor_id=actor_id,
        workspace_id=payload.workspace_id,
    )
    return WorkspaceAccessProjection.model_validate(result)


@router.delete(
    "/identity/actors/{actor_id}/memberships/{workspace_id}",
    status_code=204,
    tags=["identity"],
)
async def revoke_workspace_membership(
    request: Request,
    actor_id: str,
    workspace_id: str,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> Response:
    correlation_id = await _require_identity_permission(
        authorization,
        principal,
        "identity.memberships.manage",
        "identity.membership.revoke",
    )
    await request.app.state.auth_service.revoke_membership(
        actor_id,
        workspace_id,
        tenant_id=principal.tenant_id,
        organization_id=principal.organization_id,
    )
    await _record_identity_change(
        request,
        principal,
        correlation_id,
        event_type="identity.membership.revoked",
        actor_id=actor_id,
        workspace_id=workspace_id,
    )
    return Response(status_code=204)


@router.post(
    "/identity/actors/{actor_id}/activate",
    response_model=AccountStateProjection,
    tags=["identity"],
)
async def activate_account(
    request: Request,
    actor_id: str,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> AccountStateProjection:
    return await _change_account_state(
        request, actor_id, True, principal=principal, authorization=authorization
    )


@router.post(
    "/identity/actors/{actor_id}/suspend",
    response_model=AccountStateProjection,
    tags=["identity"],
)
async def suspend_account(
    request: Request,
    actor_id: str,
    principal: CurrentPrincipalDependency,
    authorization: AuthorizationEnforcerDependency,
) -> AccountStateProjection:
    return await _change_account_state(
        request, actor_id, False, principal=principal, authorization=authorization
    )


async def _change_account_state(
    request: Request,
    actor_id: str,
    active: bool,
    *,
    principal: Principal,
    authorization: AuthorizationEnforcer,
) -> AccountStateProjection:
    action = "activate" if active else "suspend"
    correlation_id = await _require_identity_permission(
        authorization, principal, "identity.accounts.manage", f"identity.account.{action}"
    )
    result = await request.app.state.auth_service.set_account_active(
        actor_id,
        active,
        tenant_id=principal.tenant_id,
        organization_id=principal.organization_id,
    )
    await _record_identity_change(
        request,
        principal,
        correlation_id,
        event_type=f"identity.account.{'activated' if active else 'suspended'}",
        actor_id=actor_id,
        workspace_id=principal.workspace_id,
    )
    return AccountStateProjection.model_validate(result)


async def _require_identity_permission(
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
            "AUTHORIZATION_DENIED", f"{permission} permission is required", status_code=403
        )
    return correlation_id


async def _record_identity_change(
    request: Request,
    principal: Principal,
    correlation_id: str,
    *,
    event_type: str,
    actor_id: str,
    workspace_id: str,
) -> None:
    await request.app.state.identity_audit.append(
        AuditEvent(
            event_type=event_type,
            entity_type="actor",
            entity_id=actor_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
            outcome="SUCCEEDED",
            occurred_at=datetime.now(UTC),
            reason="Governed identity authority state changed",
        )
    )


@router.post("/auth/login", response_model=AuthTokenResponse)
async def login(request: Request, payload: LoginRequest) -> AuthTokenResponse:
    service = request.app.state.auth_service
    response = await service.login(payload.email, payload.password)
    return AuthTokenResponse.model_validate(response)


@router.get("/auth/whoami", response_model=AuthenticatedPrincipalProjection)
async def whoami(request: Request) -> AuthenticatedPrincipalProjection:
    principal = await request.app.state.auth_service.whoami(_bearer_token(request))
    return AuthenticatedPrincipalProjection.model_validate(principal)


@router.post("/auth/logout", status_code=204)
async def logout(request: Request) -> Response:
    await request.app.state.auth_service.logout(_bearer_token(request))
    return Response(status_code=204)


@router.get("/workspaces", response_model=list[WorkspaceAccessProjection])
async def list_workspaces(request: Request) -> list[WorkspaceAccessProjection]:
    workspaces = await request.app.state.auth_service.list_workspaces(_bearer_token(request))
    return [WorkspaceAccessProjection.model_validate(item) for item in workspaces]


@router.put("/auth/active-workspace", response_model=ActiveWorkspaceProjection)
async def select_active_workspace(
    request: Request, payload: ActiveWorkspaceRequest
) -> ActiveWorkspaceProjection:
    selected = await request.app.state.auth_service.select_active_workspace(
        _bearer_token(request), payload.workspace_id
    )
    return ActiveWorkspaceProjection.model_validate(selected)


def _bearer_token(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise PlatformError("MISSING_TOKEN", "authorization header is required", status_code=401)
    return auth_header.split(" ", 1)[1].strip()


@router.get("/system/info", response_model=SystemInfoResponse)
async def system_info(request: Request) -> SystemInfoResponse:
    settings = request.app.state.settings
    return SystemInfoResponse(
        service="alos-backend",
        version=__version__,
        environment=settings.APP_ENV,
    )


@router.get("/system/integration")
async def integration_diagnostic(
    genesis_client: GenesisClientDependency,
    contract_validator: IntegrationContractValidatorDependency,
) -> dict[str, Any]:
    correlation_id = current_correlation_id()
    try:
        genesis_response = await genesis_client.diagnostic(correlation_id=correlation_id)
        contract_validator.validate_genesis_response(genesis_response)
        if genesis_response["correlation_id"] != correlation_id:
            raise IntegrationContractError(
                path="correlation_id",
                reason="GENESIS must return the correlation_id sent by Backend.",
            )
    except GenesisClientError as exc:
        status_code = 503 if exc.code in {"GENESIS_TIMEOUT", "GENESIS_UNAVAILABLE"} else 502
        raise PlatformError(
            exc.code,
            exc.message,
            status_code=status_code,
            retryable=exc.retryable,
            correlation_id=correlation_id,
        ) from exc
    except IntegrationContractError as exc:
        raise PlatformError(
            "GENESIS_INVALID_RESPONSE",
            "GENESIS response does not satisfy the canonical contract.",
            status_code=502,
            details={"path": exc.path, "reason": exc.reason},
            correlation_id=correlation_id,
        ) from exc

    response: dict[str, Any] = {
        "correlation_id": correlation_id,
        "status": "connected",
        "backend": {
            "service": "alos-backend",
            "status": "reachable",
            "authority": "ALOS_BACKEND",
        },
        "genesis": genesis_response,
    }
    try:
        contract_validator.validate_public_response(response)
    except IntegrationContractError as exc:
        raise PlatformError(
            "INTEGRATION_RESPONSE_INVALID",
            "Backend integration response does not satisfy the canonical contract.",
            status_code=500,
            details={"path": exc.path, "reason": exc.reason},
            correlation_id=correlation_id,
        ) from exc
    return response
