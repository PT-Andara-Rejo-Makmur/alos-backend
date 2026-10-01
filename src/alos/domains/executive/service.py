"""Executive read projection; owns no operational state or Strategy mutations."""

from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from alos.domains.strategy.authority import authorize
from alos.domains.strategy.read_model import overview
from alos.domains.strategy.service import StrategyService
from alos.identity import Principal
from alos.security.errors import PlatformError


class ExecutiveProjectionService:
    def __init__(self, strategy: StrategyService) -> None:
        self.strategy = strategy

    async def overview(self, principal: Principal) -> dict[str, Any]:
        authorize(
            principal,
            "read",
            tenant_id=principal.tenant_id,
            organization_id=principal.organization_id,
        )
        if "EXECUTIVE" not in principal.roles:
            raise PlatformError(
                "EXECUTIVE_ROLE_DENIED",
                "Executive projection requires the Executive role.",
                status_code=403,
            )
        data = None
        status = "ERROR"
        try:
            async with self.strategy.repository.transaction(
                principal.tenant_id, principal.organization_id
            ):
                data = await overview(self.strategy, principal)
            status = (
                "CONNECTED"
                if any(data[key] for key in ("plans", "objectives", "targets", "assumptions"))
                else "CONNECTED_EMPTY"
            )
        except (SQLAlchemyError, ConnectionError, TimeoutError, OSError):
            # Retrieval failure has no data payload; it is never reported as an empty source.
            pass
        updated = data["last_updated_at"] if data is not None else None
        return {
            "tenant_id": principal.tenant_id,
            "organization_id": principal.organization_id,
            "workspace_id": principal.workspace_id,
            "strategy": {
                "source": "strategy",
                "status": status,
                "authoritative": True,
                "last_updated_at": updated,
            },
            "shared_work": {
                "source": "shared_work",
                "status": "UNAVAILABLE",
                "authoritative": True,
                "last_updated_at": None,
            },
            "domains": [
                {"domain": domain, "status": "UNAVAILABLE", "sources": [], "last_verified_at": None}
                for domain in ("SALES", "FINANCE", "PROPERTY", "LEGAL", "HR", "IT")
            ],
            "last_updated_at": updated,
            "strategy_data": data,
        }
