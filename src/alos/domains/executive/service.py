"""Executive read projection; owns no operational state or Strategy mutations."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, cast

from sqlalchemy.exc import SQLAlchemyError

from alos.domains.shared_work import SharedWorkService
from alos.domains.strategy.authority import authorize
from alos.domains.strategy.read_model import overview
from alos.domains.strategy.service import StrategyService
from alos.identity import Principal
from alos.security.errors import PlatformError

if TYPE_CHECKING:
    from executive_contracts import (
        ExecutiveConnectionStatus,
        ExecutiveDomainStatus,
        ExecutiveOverviewProjection,
        ExecutiveSourceStatus,
    )
    from strategy_contracts import StrategyOverviewProjection


class BusinessOverviewPort(Protocol):
    async def overview(
        self, principal: Principal, *, executive: bool = False
    ) -> dict[str, Any]: ...


class ExecutiveProjectionService:
    def __init__(
        self,
        strategy: StrategyService,
        shared_work: SharedWorkService | None = None,
        *,
        business_sources: dict[str, tuple[tuple[str, BusinessOverviewPort], ...]] | None = None,
    ) -> None:
        self.strategy = strategy
        self.shared_work = shared_work
        self.business_sources = business_sources or {}

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
        domains: list[ExecutiveDomainStatus] = []
        business_times: list[str] = []
        for domain in ("SALES", "FINANCE", "PROPERTY", "LEGAL", "HR", "IT"):
            sources: list[ExecutiveSourceStatus] = []
            domain_status: ExecutiveConnectionStatus = "UNAVAILABLE"
            ports = self.business_sources.get(domain, ())
            if ports:
                domain_status = "CONNECTED_EMPTY"
                for source_name, port in ports:
                    try:
                        summary = await port.overview(principal, executive=True)
                        sources.append(cast("ExecutiveSourceStatus", summary["source"]))
                    except (SQLAlchemyError, ConnectionError, TimeoutError, OSError):
                        sources.append(
                            {
                                "source": source_name,
                                "status": "ERROR",
                                "authoritative": True,
                                "last_updated_at": None,
                            }
                        )
                if any(source["status"] == "ERROR" for source in sources):
                    domain_status = "ERROR"
                elif any(source["status"] == "CONNECTED" for source in sources):
                    domain_status = "CONNECTED"
            stamps = [
                source["last_updated_at"]
                for source in sources
                if source["last_updated_at"] is not None
            ]
            business_times.extend(stamps)
            domains.append(
                {
                    "domain": cast(Any, domain),
                    "status": domain_status,
                    "sources": sources,
                    "last_verified_at": max(stamps, key=datetime.fromisoformat) if stamps else None,
                }
            )
        known_times = [value for value in (updated, work_updated) if value is not None]
        known_times.extend(business_times)
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
            "domains": domains,
            "last_updated_at": last_updated,
            "strategy_data": cast("StrategyOverviewProjection | None", data),
            "shared_work_data": work_data,
        }
