"""Backend-owned research authorization and GENESIS orchestration."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from alos.audit import AuditEvent, AuditSink
from alos.contracts import CanonicalContractCatalog, ContractValidationError
from alos.identity import Principal
from alos.integrations.genesis import GenesisClient, GenesisClientError
from alos.security.errors import PlatformError

RESEARCH_ANALYSIS_REQUEST_SCHEMA = (
    "https://schemas.alos.dev/v1/research/research-analysis-request.schema.json"
)
RESEARCH_DECISION_SCHEMA = "https://schemas.alos.dev/v1/research/research-decision.schema.json"
RESEARCH_RECEIPT_SCHEMA = (
    "https://schemas.alos.dev/v1/research/research-request-receipt.schema.json"
)
RESEARCH_REQUEST_PERMISSION = "research.request"
EXTERNAL_RESEARCH_PERMISSION = "research.external.read"
EXTERNAL_RESEARCH_SCOPE = "scope.sources.external_read"
EXTERNAL_RESEARCH_TOOL = "research.external.retrieve"


class ResearchDomain(StrEnum):
    TECHNOLOGY = "TECHNOLOGY"
    PROPERTY_BUSINESS = "PROPERTY_BUSINESS"
    MANAGEMENT = "MANAGEMENT"
    PROPERTY_MARKET = "PROPERTY_MARKET"


class ResearchSourceMode(StrEnum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


DOMAIN_SCOPE: dict[ResearchDomain, str] = {
    ResearchDomain.TECHNOLOGY: "research.technology",
    ResearchDomain.PROPERTY_BUSINESS: "research.property_business",
    ResearchDomain.MANAGEMENT: "research.management",
    ResearchDomain.PROPERTY_MARKET: "research.property_market",
}


@dataclass(frozen=True, slots=True)
class ResearchCommand:
    question: str
    source_mode: ResearchSourceMode
    domain: ResearchDomain


def project_domain_access(principal: Principal, *, correlation_id: str) -> dict[str, Any]:
    domains: list[dict[str, Any]] = []
    for domain, required_scope in DOMAIN_SCOPE.items():
        allowed = (
            principal.active
            and RESEARCH_REQUEST_PERMISSION in principal.permissions
            and required_scope in principal.scopes
        )
        domains.append(
            {
                "domain": domain.value,
                "status": "AUTHORIZED" if allowed else "DENIED",
                "is_allowed": allowed,
                "reason": (
                    "Authorized by ALOS Backend policy."
                    if allowed
                    else "The principal lacks the required Backend permission or domain scope."
                ),
                "required_scope": required_scope,
            }
        )
    return {"domains": domains, "correlation_id": correlation_id}


class ResearchService:
    def __init__(
        self,
        *,
        contracts: CanonicalContractCatalog,
        genesis: GenesisClient,
        audit: AuditSink,
    ) -> None:
        self._contracts = contracts
        self._genesis = genesis
        self._audit = audit

    async def request(
        self,
        command: ResearchCommand,
        *,
        principal: Principal,
        correlation_id: str,
    ) -> dict[str, Any]:
        required_scope = DOMAIN_SCOPE[command.domain]
        if (
            not principal.active
            or RESEARCH_REQUEST_PERMISSION not in principal.permissions
            or required_scope not in principal.scopes
        ):
            await self._audit.append(
                self._event(
                    principal,
                    correlation_id=correlation_id,
                    domain=command.domain,
                    outcome="DENIED",
                    decision="SCOPE_DENIED",
                )
            )
            raise PlatformError(
                "RESEARCH_SCOPE_DENIED",
                "The principal is not authorized for the requested research domain.",
                status_code=403,
                correlation_id=correlation_id,
            )

        allowed_tools: list[str] = []
        permission_refs = [RESEARCH_REQUEST_PERMISSION]
        scope_refs = [required_scope]
        if (
            command.source_mode is ResearchSourceMode.EXTERNAL
            and EXTERNAL_RESEARCH_PERMISSION in principal.permissions
            and EXTERNAL_RESEARCH_SCOPE in principal.scopes
        ):
            allowed_tools.append(EXTERNAL_RESEARCH_TOOL)
            permission_refs.append(EXTERNAL_RESEARCH_PERMISSION)
            scope_refs.append(EXTERNAL_RESEARCH_SCOPE)

        role_refs = sorted(principal.roles)
        payload: dict[str, Any] = {
            "question": command.question,
            "source_mode": command.source_mode.value,
            "domain": command.domain.value,
            "execution_context": {
                "tenant_id": principal.tenant_id,
                "organization_id": principal.organization_id,
                "workspace_id": principal.workspace_id,
                "actor_id": principal.actor_id,
                "authority_context": {
                    "role": role_refs[0] if role_refs else "AUTHENTICATED_USER",
                    "role_refs": role_refs,
                    "authority_level": "REQUESTER",
                },
                "permission_refs": permission_refs,
                "allowed_tool_ids": allowed_tools,
                "scope_refs": scope_refs,
                "data_classification": "INTERNAL",
                "correlation_id": correlation_id,
                "execution_budget": {
                    "max_cost": 0,
                    "max_tool_calls": 1 if allowed_tools else 0,
                    "timeout_seconds": 30,
                },
            },
        }
        try:
            validated_request = self._contracts.validate(
                RESEARCH_ANALYSIS_REQUEST_SCHEMA, payload
            )
            decision = await self._genesis.research(
                validated_request,
                correlation_id=correlation_id,
            )
            validated_decision = self._contracts.validate(RESEARCH_DECISION_SCHEMA, decision)
        except ContractValidationError as exc:
            raise PlatformError(
                "RESEARCH_CONTRACT_INVALID",
                "Research boundary payload failed canonical contract validation.",
                status_code=502,
                correlation_id=correlation_id,
                details={"path": exc.path, "reason": exc.reason},
            ) from exc
        except GenesisClientError as exc:
            status_code = 503 if exc.code in {"GENESIS_TIMEOUT", "GENESIS_UNAVAILABLE"} else 502
            raise PlatformError(
                exc.code,
                exc.message,
                status_code=status_code,
                retryable=exc.retryable,
                correlation_id=correlation_id,
            ) from exc

        if validated_decision["correlation_id"] != correlation_id:
            raise PlatformError(
                "RESEARCH_CORRELATION_MISMATCH",
                "GENESIS research decision correlation does not match the Backend request.",
                status_code=502,
                correlation_id=correlation_id,
            )
        decision_kind = str(validated_decision["decision"])
        state = (
            "NEEDS_REVIEW"
            if decision_kind
            in {"REQUEST_EXTERNAL_RESEARCH", "INSUFFICIENT_EVIDENCE", "NEEDS_INFORMATION"}
            else "RECEIVED"
        )
        request_id = f"research_{hashlib.sha256(correlation_id.encode()).hexdigest()[:24]}"
        receipt = {
            "request_id": request_id,
            "state": state,
            "correlation_id": correlation_id,
            "decision": decision_kind,
        }
        validated_receipt = self._contracts.validate(RESEARCH_RECEIPT_SCHEMA, receipt)
        await self._audit.append(
            self._event(
                principal,
                correlation_id=correlation_id,
                domain=command.domain,
                outcome="SUCCESS",
                decision=decision_kind,
            )
        )
        return validated_receipt

    @staticmethod
    def _event(
        principal: Principal,
        *,
        correlation_id: str,
        domain: ResearchDomain,
        outcome: str,
        decision: str,
    ) -> AuditEvent:
        entity_id = f"research_{hashlib.sha256(correlation_id.encode()).hexdigest()[:24]}"
        return AuditEvent(
            event_type="research.request",
            entity_type="research_request",
            entity_id=entity_id,
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
            workspace_id=principal.workspace_id,
            actor_id=principal.actor_id,
            correlation_id=correlation_id,
            outcome=outcome,
            occurred_at=datetime.now(UTC),
            reason="Backend-governed research decision",
            metadata={"domain": domain.value, "decision": decision},
        )
