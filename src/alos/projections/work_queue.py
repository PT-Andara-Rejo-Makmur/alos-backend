"""Current workspace work lists reuse canonical owner services and permission checks."""

from typing import Any

from alos.domains.shared_work import SharedWorkService
from alos.identity import Principal
from alos.notifications.business import BusinessNotifications
from alos.processes.service import ProcessService


async def work_queue(
    work: SharedWorkService, processes: ProcessService, principal: Principal
) -> dict[str, Any]:
    tasks, approvals, findings = [], [], []
    if {"task.read", "work.read"} & principal.permissions:
        tasks = [
            row
            for row in await work.list_tasks(principal, status=None, priority=None, search=None)
            if row["status"] not in {"COMPLETED", "CANCELLED"}
            and (row["owner_actor_id"] == principal.actor_id or "DIVISION_LEAD" in principal.roles)
        ]
    if {"approval.read", "work.read"} & principal.permissions:
        approvals = [
            row
            for row in await work.list_approvals(
                principal, status=None, subject_type=None, search=None
            )
            if row["status"] in {"PENDING", "RETURNED", "HOLD"}
        ]
    if {"finding.read", "work.read"} & principal.permissions:
        findings = [
            row
            for row in await work.list_findings(
                principal, status=None, severity=None, source_type=None, search=None
            )
            if row["status"] not in {"VERIFIED", "CLOSED", "CANCELLED"}
            and (row["owner_actor_id"] == principal.actor_id or "DIVISION_LEAD" in principal.roles)
        ]
    return {
        "processes": (await processes.queue(principal))["items"],
        "tasks": await work.present(principal, "TASK", tasks),
        "approvals": await work.present(principal, "APPROVAL", approvals),
        "findings": await work.present(principal, "FINDING", findings),
        "notifications": (await BusinessNotifications(processes.repository).list(principal))[
            "items"
        ],
    }
