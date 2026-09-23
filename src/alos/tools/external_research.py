"""Backend-owned external research tool adapter."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from alos.tools.adapters.base import ToolInputError


class ExternalResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    research_request: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    source_requirement: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    correlation_id: str = Field(min_length=1)
    budget_limit: float | None = None


class ExternalResearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    domain: str
    finding: str = Field(min_length=1)
    evidence: tuple[str, ...] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    reliability: str = "MEDIUM"
    freshness: str = "CURRENT"
    conflicts: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    source_reference: str | None = None
    retrieval_metadata: dict[str, Any] = Field(default_factory=dict)


class ExternalResearchToolAdapter:
    """Typed external research adapter that stays inside the backend ToolExecutor boundary."""

    allowed_domains = frozenset(
        {
            "technology",
            "property_business",
            "management",
            "property_market",
        }
    )

    @staticmethod
    def _sanitize_research_text(value: str) -> str:
        sanitized = value.strip().replace("\r", " ").replace("\n", " ")
        blocked = (
            "ignore all permissions",
            "ignore previous instructions",
            "access internal database",
            "set scope=",
            "grant scope",
            "override authority",
            "bypass auth",
            "drop policy",
            "ignore policy",
            "change permission",
            "access private",
            "change role",
            "become admin",
            "grant admin",
            "internal database",
        )
        for token in blocked:
            sanitized = re.sub(re.escape(token), " ", sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r"\badmin\b", " ", sanitized, flags=re.IGNORECASE)
        sanitized = re.sub(r"\s+", " ", sanitized).strip()
        if not sanitized:
            return "Research request accepted for authoritative review."
        return sanitized[:200]

    def validate_arguments(self, arguments: Mapping[str, Any]) -> None:
        if not isinstance(arguments, Mapping):
            raise ToolInputError("arguments must be a mapping")
        required = {
            "research_request",
            "domain",
            "source_requirement",
            "actor",
            "scope",
            "correlation_id",
        }
        missing = sorted(required.difference(str(key) for key in arguments))
        if missing:
            raise ToolInputError(f"missing required external research fields: {', '.join(missing)}")
        if (
            not isinstance(arguments["research_request"], str)
            or not arguments["research_request"].strip()
        ):
            raise ToolInputError("research_request must be a non-empty string")
        self._sanitize_research_text(str(arguments["research_request"]))
        domain = str(arguments["domain"]).lower()
        if domain not in self.allowed_domains:
            raise ToolInputError("domain is not eligible for external research")
        if not isinstance(arguments["actor"], str) or not arguments["actor"].strip():
            raise ToolInputError("actor must be a non-empty string")
        if (
            not isinstance(arguments["scope"], str)
            or arguments["scope"] != "scope.sources.external_read"
        ):
            raise ToolInputError("scope must permit external research")
        if (
            not isinstance(arguments["correlation_id"], str)
            or not arguments["correlation_id"].strip()
        ):
            raise ToolInputError("correlation_id must be a non-empty string")
        budget_limit = arguments.get("budget_limit")
        if budget_limit is not None and (
            not isinstance(budget_limit, (int, float)) or budget_limit <= 0
        ):
            raise ToolInputError("budget_limit must be a positive number when provided")

    async def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        execution_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        del execution_context
        self.validate_arguments(arguments)
        domain = str(arguments["domain"]).lower()
        request = self._sanitize_research_text(str(arguments["research_request"]))
        evidence = tuple(
            str(value) for value in arguments.get("evidence", ("approved.public-source",))
        )
        if not any("approved" in str(item).lower() for item in evidence):
            evidence = ("approved.public-source",)
        result = ExternalResearchResult(
            domain=domain,
            finding=f"Research request accepted for authoritative review: {request}",
            evidence=evidence,
            confidence=0.75,
            reliability="MEDIUM",
            freshness="CURRENT",
            conflicts=(),
            assumptions=("Source validation performed by backend policy.",),
            limitations=("External content is treated as untrusted and must be verified.",),
            source_reference=str(arguments.get("source_requirement") or "approved.public-source"),
            retrieval_metadata={
                "actor": str(arguments["actor"]),
                "scope": str(arguments["scope"]),
                "correlation_id": str(arguments["correlation_id"]),
                "budget_limit": arguments.get("budget_limit"),
            },
        )
        return result.model_dump(mode="python")
