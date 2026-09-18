"""Build an authenticated principal from a validated canonical ExecutionContext."""

from collections.abc import Mapping
from typing import Any

from alos.contracts import CanonicalContractCatalog
from alos.identity import DataScope, Principal

EXECUTION_CONTEXT_SCHEMA_ID = "https://schemas.alos.dev/v1/common/execution-context.schema.json"


class PrincipalFactory:
    def __init__(self, contracts: CanonicalContractCatalog) -> None:
        self._contracts = contracts

    def from_execution_context(self, context: Mapping[str, Any]) -> Principal:
        validated = self._contracts.validate(EXECUTION_CONTEXT_SCHEMA_ID, context)
        authority = validated["authority_context"]
        assert isinstance(authority, dict)
        roles = {str(authority["role"])}
        roles.update(str(value) for value in authority.get("role_refs", []))
        return Principal(
            actor_id=str(validated["actor_id"]),
            tenant_id=str(validated["tenant_id"]),
            organization_id=str(validated["organization_id"]),
            workspace_id=str(validated["workspace_id"]),
            permissions=frozenset(str(value) for value in validated.get("permission_refs", [])),
            scopes=frozenset(str(value) for value in validated["scope_refs"]),
            roles=frozenset(roles),
            data_scope=DataScope.OWN_ASSIGNED,
        )
