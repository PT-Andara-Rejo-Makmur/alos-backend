"""Orchestrate Factory analysis while retaining identity and registry authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from alos.agents.registry import AgentRegistry
from alos.capabilities.registry import CapabilityRegistry
from alos.contracts import CanonicalContractCatalog, ContractValidationError
from alos.identity import Principal
from alos.integrations.genesis import GenesisClientError
from alos.registry import RegistryConflictError, RegistryEntry
from alos.security.errors import PlatformError

PUBLIC_REQUEST_SCHEMA = "https://schemas.alos.dev/v1/factory/factory-analyze-request.schema.json"
INTERNAL_REQUEST_SCHEMA = (
    "https://schemas.alos.dev/v1/factory/factory-analysis-request.schema.json"
)
INTERNAL_RESULT_SCHEMA = (
    "https://schemas.alos.dev/v1/factory/factory-analysis-result.schema.json"
)
PUBLIC_RESPONSE_SCHEMA = (
    "https://schemas.alos.dev/v1/factory/factory-analyze-response.schema.json"
)


class FactoryGenesisClient(Protocol):
    async def analyze_factory(
        self, payload: Mapping[str, Any], *, correlation_id: str
    ) -> dict[str, Any]: ...


class FactoryOrchestrator:
    """Own authority context and persist only canonical CREATE proposals as DRAFT."""

    def __init__(
        self,
        *,
        contracts: CanonicalContractCatalog,
        genesis: FactoryGenesisClient,
        capabilities: CapabilityRegistry,
        agents: AgentRegistry,
    ) -> None:
        self._contracts = contracts
        self._genesis = genesis
        self._capabilities = capabilities
        self._agents = agents

    async def analyze(
        self,
        payload: Mapping[str, Any],
        *,
        principal: Principal,
        correlation_id: str,
    ) -> dict[str, Any]:
        public_request = self._validate_client_request(payload, correlation_id)
        if not principal.active:
            raise PlatformError(
                "INACTIVE_PRINCIPAL",
                "The authenticated principal is inactive.",
                status_code=403,
                correlation_id=correlation_id,
            )
        if not principal.scopes:
            raise PlatformError(
                "FACTORY_SCOPE_REQUIRED",
                "At least one Backend-authorized scope is required.",
                status_code=403,
                correlation_id=correlation_id,
            )

        internal_request: dict[str, Any] = {
            "requirement": {
                "execution_context": self._execution_context(principal, correlation_id),
                "requirement_id": f"req_{correlation_id}",
                "statement": public_request["requirement"],
            },
            "capability_catalog": list(
                self._capabilities.catalog_snapshot(principal=principal)
            ),
        }
        preferred = public_request.get("preferred_capability_type")
        if preferred is not None:
            internal_request["requirement"]["preferred_capability_type"] = preferred
        self._contracts.validate(INTERNAL_REQUEST_SCHEMA, internal_request)

        try:
            result = await self._genesis.analyze_factory(
                internal_request,
                correlation_id=correlation_id,
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

        try:
            validated_result = self._contracts.validate(INTERNAL_RESULT_SCHEMA, result)
        except (ContractValidationError, ValueError) as exc:
            raise PlatformError(
                "GENESIS_FACTORY_RESPONSE_INVALID",
                "GENESIS Factory response does not satisfy the canonical contract.",
                status_code=502,
                details={"reason": str(exc)},
                correlation_id=correlation_id,
            ) from exc
        if validated_result["correlation_id"] != correlation_id:
            raise PlatformError(
                "GENESIS_CORRELATION_MISMATCH",
                "GENESIS must preserve the Backend correlation_id.",
                status_code=502,
                correlation_id=correlation_id,
            )

        resolution = validated_result["resolution"]
        decision = resolution["decision"]
        self._require_requirement_linkage(resolution, correlation_id)
        self._fail_closed_on_ambiguity(resolution, correlation_id)
        if decision == "REUSE":
            response = {
                "correlation_id": correlation_id,
                "decision": "REUSE",
                "reason": resolution["reason"],
                "existing_capability_refs": validated_result["existing_capability_refs"],
                "capability_draft": None,
                "agent_draft": None,
                "registry_result": None,
            }
            return self._contracts.validate(PUBLIC_RESPONSE_SCHEMA, response)

        capability_draft = validated_result["capability_draft"]
        if not isinstance(capability_draft, dict):
            raise PlatformError(
                "GENESIS_FACTORY_RESPONSE_INVALID",
                "CREATE requires a canonical CapabilityDraft.",
                status_code=502,
                correlation_id=correlation_id,
            )
        self._require_human_gate(capability_draft, correlation_id)
        self._require_authority_context(capability_draft, principal, correlation_id)
        self._authorize_draft(capability_draft, principal, correlation_id)
        if capability_draft.get("owner") != principal.actor_id:
            raise PlatformError(
                "GENESIS_OWNER_MISMATCH",
                "GENESIS cannot assign capability ownership outside Backend authority.",
                status_code=502,
                correlation_id=correlation_id,
            )
        agent_draft = validated_result["agent_draft"]
        if isinstance(agent_draft, dict):
            self._require_authority_context(agent_draft, principal, correlation_id)
            self._authorize_draft(agent_draft, principal, correlation_id)
            definition = agent_draft.get("agent_definition")
            if (
                not isinstance(definition, dict)
                or definition.get("owner_actor_id") != principal.actor_id
            ):
                raise PlatformError(
                    "GENESIS_OWNER_MISMATCH",
                    "GENESIS cannot assign agent ownership outside Backend authority.",
                    status_code=502,
                    correlation_id=correlation_id,
                )
            self._authorize_draft(definition, principal, correlation_id)

        try:
            registered = [
                await self._capabilities.register(
                    self._capability_definition(capability_draft),
                    tenant_id=principal.tenant_id,
                    organization_id=principal.organization_id,
                    workspace_id=principal.workspace_id,
                    actor_id=principal.actor_id,
                    correlation_id=correlation_id,
                )
            ]
            if isinstance(agent_draft, dict):
                definition = agent_draft.get("agent_definition")
                if not isinstance(definition, dict):
                    raise PlatformError(
                        "GENESIS_FACTORY_RESPONSE_INVALID",
                        "AgentDraft must contain a canonical agent_definition.",
                        status_code=502,
                        correlation_id=correlation_id,
                    )
                registered.append(
                    await self._agents.register(
                        definition,
                        tenant_id=principal.tenant_id,
                        organization_id=principal.organization_id,
                        workspace_id=principal.workspace_id,
                        actor_id=principal.actor_id,
                        correlation_id=correlation_id,
                    )
                )
        except RegistryConflictError as exc:
            raise PlatformError(
                "FACTORY_REGISTRY_CONFLICT",
                "The proposed immutable registry version conflicts with an existing version.",
                status_code=409,
                details={"reason": str(exc)},
                correlation_id=correlation_id,
            ) from exc
        except ContractValidationError as exc:
            raise PlatformError(
                "FACTORY_REGISTRY_CONTRACT_INVALID",
                "The proposed registry definition is invalid.",
                status_code=502,
                details={"reason": str(exc)},
                correlation_id=correlation_id,
            ) from exc

        response = {
            "correlation_id": correlation_id,
            "decision": "CREATE",
            "reason": resolution["reason"],
            "existing_capability_refs": [],
            "capability_draft": capability_draft,
            "agent_draft": agent_draft,
            "registry_result": {
                "state": "DRAFT",
                "registered_refs": [self._registry_reference(entry) for entry in registered],
            },
        }
        return self._contracts.validate(PUBLIC_RESPONSE_SCHEMA, response)

    def _validate_client_request(
        self, payload: Mapping[str, Any], correlation_id: str
    ) -> dict[str, Any]:
        try:
            return self._contracts.validate(PUBLIC_REQUEST_SCHEMA, payload)
        except ContractValidationError as exc:
            raise PlatformError(
                "FACTORY_REQUEST_INVALID",
                "Factory request does not satisfy the canonical contract.",
                status_code=422,
                details={"path": exc.path, "reason": exc.reason},
                correlation_id=correlation_id,
            ) from exc

    @staticmethod
    def _execution_context(principal: Principal, correlation_id: str) -> dict[str, Any]:
        roles = sorted(principal.roles)
        return {
            "tenant_id": principal.tenant_id,
            "organization_id": principal.organization_id,
            "workspace_id": principal.workspace_id,
            "actor_id": principal.actor_id,
            "authority_context": {
                "role": roles[0] if roles else "AUTHENTICATED_USER",
                "role_refs": roles,
                "authority_level": "REQUESTER",
            },
            "permission_refs": sorted(principal.permissions),
            "scope_refs": sorted(principal.scopes),
            "data_classification": "INTERNAL",
            "correlation_id": correlation_id,
        }

    @staticmethod
    def _require_requirement_linkage(
        resolution: Mapping[str, Any], correlation_id: str
    ) -> None:
        """Backend-owned requirement_id must be echoed unchanged by GENESIS."""

        expected = f"req_{correlation_id}"
        understanding = resolution.get("understanding")
        supplied = understanding.get("requirement_id") if isinstance(understanding, dict) else None
        if supplied is not None and supplied != expected:
            raise PlatformError(
                "GENESIS_FACTORY_RESPONSE_INVALID",
                "GENESIS altered the Backend-owned requirement linkage.",
                status_code=502,
                correlation_id=correlation_id,
            )

    @staticmethod
    def _fail_closed_on_ambiguity(
        resolution: Mapping[str, Any], correlation_id: str
    ) -> None:
        """An ambiguous requirement is a needs-info state; no authority or state is created."""

        understanding = resolution.get("understanding")
        ambiguity = understanding.get("ambiguity") if isinstance(understanding, dict) else None
        if ambiguity == "NEEDS_CLARIFICATION":
            raise PlatformError(
                "REQUIREMENT_AMBIGUOUS",
                (
                    "The requirement needs more information before a capability proposal "
                    "can be governed."
                ),
                status_code=422,
                details={"ambiguity": ambiguity},
                correlation_id=correlation_id,
            )

    @staticmethod
    def _require_human_gate(draft: Mapping[str, Any], correlation_id: str) -> None:
        """Backend governance mandates the human gate; AI proposals cannot remove it."""

        if draft.get("human_gate_required") is False:
            raise PlatformError(
                "GENESIS_HUMAN_GATE_REQUIRED",
                "Backend governance requires a human gate; the proposal cannot remove it.",
                status_code=502,
                correlation_id=correlation_id,
            )

    @staticmethod
    def _require_authority_context(
        draft: Mapping[str, Any], principal: Principal, correlation_id: str
    ) -> None:
        expected = {
            "tenant_id": principal.tenant_id,
            "organization_id": principal.organization_id,
            "workspace_id": principal.workspace_id,
            "correlation_id": correlation_id,
        }
        mismatches = [name for name, value in expected.items() if draft.get(name) != value]
        if mismatches:
            raise PlatformError(
                "GENESIS_AUTHORITY_CONTEXT_MISMATCH",
                "GENESIS draft changed Backend-owned authority context.",
                status_code=502,
                details={"fields": mismatches},
                correlation_id=correlation_id,
            )

    @staticmethod
    def _authorize_draft(
        draft: Mapping[str, Any], principal: Principal, correlation_id: str
    ) -> None:
        permissions = {str(item) for item in draft.get("permission_refs", [])}
        scopes = {str(item) for item in draft.get("scope_refs", [])}
        missing_permissions = sorted(permissions.difference(principal.permissions))
        unauthorized_scopes = sorted(scopes.difference(principal.scopes))
        if missing_permissions or unauthorized_scopes:
            raise PlatformError(
                "FACTORY_PROPOSAL_NOT_AUTHORIZED",
                "The actor lacks permission or scope required by the proposed capability.",
                status_code=403,
                details={
                    "missing_permission_refs": missing_permissions,
                    "unauthorized_scope_refs": unauthorized_scopes,
                },
                correlation_id=correlation_id,
            )

    @staticmethod
    def _capability_definition(draft: Mapping[str, Any]) -> dict[str, Any]:
        """Convert a proposal to a governed definition whose registry state remains DRAFT."""

        return {
            "tenant_id": draft["tenant_id"],
            "organization_id": draft["organization_id"],
            "workspace_id": draft["workspace_id"],
            "correlation_id": draft["correlation_id"],
            "capability_id": draft["capability_id"],
            "version": draft["version"],
            "name": draft["name"],
            "purpose": draft["purpose"],
            "owner": draft["owner"],
            "capability_type": draft["capability_type"],
            "lifecycle_state": "DEFINED",
            "risk_level": draft["risk_level"],
            "access_mode": "CREATE_DRAFT",
            "availability": "UNAVAILABLE",
            "configuration_status": "NEEDS_CONFIGURATION",
            "scope_refs": draft["scope_refs"],
            "permission_refs": draft["permission_refs"],
            "backing_tool_ids": draft["tool_ids"],
            "constraints": draft.get("constraints", []),
            "metadata": {
                "proposal_lifecycle_state": draft["lifecycle_state"],
                "prohibited_actions": draft["prohibited_actions"],
                "evidence_requirements": draft["evidence_requirements"],
                "test_requirements": draft["test_requirements"],
                "human_gate_required": True,
                "dependency_refs": list(draft.get("dependency_refs", [])),
            },
        }

    @staticmethod
    def _registry_reference(entry: RegistryEntry) -> dict[str, str]:
        return {
            "subject_type": entry.subject_type.upper(),
            "identifier": entry.subject_id,
            "version": entry.version,
            "state": entry.state.value,
        }
