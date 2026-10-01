"""Executive read projection; owns no operational state or Strategy mutations."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy.exc import SQLAlchemyError

from alos.domains.shared_work import SharedWorkService
from alos.domains.strategy.authority import authorize
from alos.domains.strategy.read_model import overview
from alos.domains.strategy.service import StrategyService
from alos.identity import Principal
from alos.security.errors import PlatformError

if TYPE_CHECKING:
    from executive_contracts import ExecutiveConnectionStatus, ExecutiveOverviewProjection
    from strategy_contracts import StrategyOverviewProjection


class ExecutiveProjectionService:
    def __init__(
        self, strategy: StrategyService, shared_work: SharedWorkService | None = None
    ) -> None:
        self.strategy = strategy
        self.shared_work = shared_work

    async def overview(self, principal: Principal) -> ExecutiveOverviewProjection:
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
        status: ExecutiveConnectionStatus = "ERROR"
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
        work_data = None
        work_status: ExecutiveConnectionStatus = "UNAVAILABLE"
        if self.shared_work is not None:
            work_status = "ERROR"
            try:
                work_data = await self.shared_work.executive_summary(principal)
                counts = work_data["counts"]
                has_work = any(
                    (
                        counts["projects"],
                        counts["tasks"],
                        counts["approvals"],
                        counts["findings"],
                        counts["reports"],
                        counts["documents"],
                    )
                )
                work_status = "CONNECTED" if has_work else "CONNECTED_EMPTY"
            except (SQLAlchemyError, ConnectionError, TimeoutError, OSError):
                pass
        work_updated = work_data["last_updated_at"] if work_data is not None else None
        known_times = [value for value in (updated, work_updated) if value is not None]
        last_updated = max(known_times, key=datetime.fromisoformat) if known_times else None
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
                "status": work_status,
                "authoritative": True,
                "last_updated_at": work_updated,
            },
            "domains": [
                {"domain": domain, "status": "UNAVAILABLE", "sources": [], "last_verified_at": None}
                for domain in ("SALES", "FINANCE", "PROPERTY", "LEGAL", "HR", "IT")
            ],
            "last_updated_at": last_updated,
            "strategy_data": cast("StrategyOverviewProjection | None", data),
            "shared_work_data": work_data,
        }
