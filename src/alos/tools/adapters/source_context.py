"""Governed read-only adapter for Backend-owned source and evidence authority."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from alos.documents.models import DataClassification
from alos.sources import KnowledgeAccessContext, SourceRegistry
from alos.tools.adapters.base import ToolInputError


class SourceContextSearchAdapter:
    """Retrieve canonical context; GENESIS never receives a database connection."""

    def __init__(self, sources: SourceRegistry) -> None:
        self._sources = sources

    def validate_arguments(self, arguments: Mapping[str, Any]) -> None:
        allowed = {"query", "limit", "max_characters"}
        if not set(arguments).issubset(allowed):
            raise ToolInputError("source.search_context received an unknown argument")
        query = arguments.get("query")
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 10_000:
            raise ToolInputError("query must be a non-blank string of at most 10000 characters")
        limit = arguments.get("limit", 12)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ToolInputError("limit must be an integer between 1 and 50")
        max_characters = arguments.get("max_characters", 12_000)
        if (
            not isinstance(max_characters, int)
            or isinstance(max_characters, bool)
            or not 1 <= max_characters <= 60_000
        ):
            raise ToolInputError("max_characters must be an integer between 1 and 60000")

    async def execute(
        self,
        arguments: Mapping[str, Any],
        *,
        execution_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        access = KnowledgeAccessContext(
            tenant_id=str(execution_context["tenant_id"]),
            organization_id=str(execution_context["organization_id"]),
            workspace_id=str(execution_context["workspace_id"]),
            actor_id=str(execution_context["actor_id"]),
            correlation_id=str(execution_context["correlation_id"]),
            scope_refs=frozenset(str(item) for item in execution_context["scope_refs"]),
            data_classification=cast(
                DataClassification,
                str(execution_context["data_classification"]),
            ),
        )
        return await self._sources.search_context(
            str(arguments["query"]),
            access=access,
            limit=int(arguments.get("limit", 12)),
            max_characters=int(arguments.get("max_characters", 12_000)),
        )
