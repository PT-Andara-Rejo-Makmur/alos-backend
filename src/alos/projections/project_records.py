"""Project context reads existing domain references under each current permission."""

from typing import Any

from sqlalchemy import select

from alos.domains.finance.records import SPECS as FINANCE
from alos.domains.hr.records import SPECS as HR
from alos.domains.it.records import SPECS as IT
from alos.domains.legal.records import SPECS as LEGAL
from alos.domains.property.records import SPECS as PROPERTY
from alos.domains.record_repository import RecordRepository, authorize
from alos.domains.sales.records import SPECS as SALES
from alos.domains.shared_work import SharedWorkService
from alos.identity import Principal
from alos.processes.authority import revalidate
from alos.security.errors import PlatformError

DOMAINS = {
    "sales": SALES,
    "property": PROPERTY,
    "finance": FINANCE,
    "legal": LEGAL,
    "hr": HR,
    "it": IT,
}


async def project_records(
    repository: RecordRepository, work: SharedWorkService, principal: Principal, project_id: str
) -> dict[str, Any]:
    await work.get_project(principal, project_id)
    records = []
    async with repository.factory() as session, session.begin():
        await revalidate(repository, session, principal)
        await work.validate_project_reference(session, principal, project_id)
        for domain, specs in DOMAINS.items():
            if f"{domain}.read" not in principal.permissions:
                continue
            try:
                authorize(principal, domain, "read")
            except PlatformError:
                continue
            for resource, spec in specs.items():
                table = await repository.table(session, domain, spec.table)
                if "project_id" not in table.c:
                    continue
                rows = (
                    (
                        await session.execute(
                            select(table)
                            .where(
                                *repository.scope(table, principal),
                                table.c.project_id == project_id,
                            )
                            .order_by(table.c.updated_at.desc())
                            .limit(50)
                        )
                    )
                    .mappings()
                    .all()
                )
                for row in rows:
                    title = next(
                        (
                            str(row[field])
                            for field in (
                                "title",
                                "name",
                                "reference",
                                "change_number",
                                "certificate_number",
                                "package_code",
                                "milestone_code",
                                "contract_number",
                                "description",
                            )
                            if row.get(field)
                        ),
                        resource.replace("_", " "),
                    )
                    records.append(
                        {
                            "domain": domain,
                            "resource": resource,
                            "record_id": row[spec.identifier],
                            "label": title[:200],
                            "status": str(row.get(spec.status_field, "RECORDED")),
                        }
                    )
    return {"project_id": project_id, "items": records}
