"""Fixed non-production authority fixture for the diagnostic tool boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from alos.identity import Principal


class DiagnosticPrincipalResolver:
    """Resolve only the documented diagnostic actor/context; deny everything else."""

    TENANT_ID = "tenant_diagnostic_001"
    ORGANIZATION_ID = "org_diagnostic_001"
    WORKSPACE_ID = "workspace_diagnostic_001"
    ACTOR_ID = "actor_diagnostic_001"

    def resolve(self, context: Mapping[str, Any]) -> Principal | None:
        expected = {
            "tenant_id": self.TENANT_ID,
            "organization_id": self.ORGANIZATION_ID,
            "workspace_id": self.WORKSPACE_ID,
            "actor_id": self.ACTOR_ID,
        }
        if any(context.get(key) != value for key, value in expected.items()):
            return None
        return Principal(
            actor_id=self.ACTOR_ID,
            tenant_id=self.TENANT_ID,
            organization_id=self.ORGANIZATION_ID,
            workspace_id=self.WORKSPACE_ID,
            permissions=frozenset({"tools.diagnostic.execute"}),
            scopes=frozenset({"scope.diagnostic"}),
        )
