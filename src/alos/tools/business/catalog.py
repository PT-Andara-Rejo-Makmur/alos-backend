"""Semantic read catalog delegates business rules to the owning domain service."""

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi.encoders import jsonable_encoder

from alos.identity import DataScope, Principal
from alos.tools.adapters.base import ToolInputError
from alos.tools.registry import ToolRegistration, ToolRegistry

# tool id -> domain, owner operation, canonical resource, required permission
BUSINESS_TOOLS: dict[str, tuple[str, str, str, str]] = {
    "executive.overview.read": ("executive", "overview", "", "strategy.read"),
    "strategy.plan.read": ("strategy", "list_plans", "", "strategy.read"),
    "strategy.target.read": ("strategy", "list_targets", "", "strategy.read"),
    "shared.project.list": ("shared_work", "list_projects", "", "project.read"),
    "shared.project.read": ("shared_work", "get_project", "project_id", "project.read"),
    "shared.task.list": ("shared_work", "list_tasks", "", "task.read"),
    "shared.task.read": ("shared_work", "get_task", "task_id", "task.read"),
    "shared.approval.list": ("shared_work", "list_approvals", "", "approval.read"),
    "shared.document.read": ("shared_work", "get_document", "document_id", "document.read"),
    "shared.finding.list": ("shared_work", "list_findings", "", "finding.read"),
    "shared.report.read": ("shared_work", "get_report", "report_id", "report.read"),
    "sales.overview.read": ("sales", "overview", "", "sales.read"),
    "sales.lead.list": ("sales", "listing", "leads", "sales.read"),
    "sales.opportunity.list": ("sales", "listing", "opportunities", "sales.read"),
    "sales.booking.read": ("sales", "detail", "bookings", "sales.read"),
    "marketing.overview.read": ("marketing", "overview", "", "marketing.read"),
    "marketing.campaign.list": ("marketing", "listing", "campaigns", "marketing.read"),
    "property.overview.read": ("property", "overview", "", "property.read"),
    "property.unit.list": ("property", "listing", "property_units", "property.read"),
    "property.progress.read": ("property", "listing", "construction_updates", "property.read"),
    "finance.overview.read": ("finance", "overview", "", "finance.read"),
    "finance.receivable.list": ("finance", "listing", "receivables", "finance.read"),
    "finance.payable.list": ("finance", "listing", "payables", "finance.read"),
    "finance.budget.read": ("finance", "detail", "budgets", "finance.read"),
    "hr.overview.read": ("hr", "overview", "", "hr.read"),
    "hr.employee.summary": ("hr", "listing", "employees", "hr.read"),
    "hr.recruitment.summary": ("hr", "listing", "recruitments", "hr.read"),
    "legal.overview.read": ("legal", "overview", "", "legal.read"),
    "legal.risk.list": ("legal", "listing", "risks", "legal.read"),
    "legal.contract.read": ("legal", "detail", "contracts", "legal.read"),
    "it.overview.read": ("it", "overview", "", "it.read"),
    "it.incident.list": ("it", "listing", "incidents", "it.read"),
    "it.system.list": ("it", "listing", "systems", "it.read"),
}


def principal_from_context(context: Mapping[str, Any]) -> Principal:
    authority = context.get("authority_context", {})
    return Principal(
        actor_id=str(context["actor_id"]),
        tenant_id=str(context["tenant_id"]),
        organization_id=str(context["organization_id"]),
        workspace_id=str(context["workspace_id"]),
        permissions=frozenset(context.get("permission_refs", [])),
        scopes=frozenset(context.get("scope_refs", [])),
        roles=frozenset(authority.get("role_refs", [])),
        data_scope=DataScope(context.get("data_scope", "OWN_ASSIGNED")),
        division_id=context.get("division_id"),
        project_id=context.get("project_id"),
    )


def business_tool_allowed(tool_id: str, principal: Principal) -> bool:
    spec = BUSINESS_TOOLS.get(tool_id)
    if not spec or not principal.active or not principal.scopes:
        return False
    domain, operation, _, permission = spec
    executive = "EXECUTIVE" in principal.roles and principal.data_scope == DataScope.COMPANY
    if executive and operation == "overview" and "strategy.read" in principal.permissions:
        return True
    if permission not in principal.permissions:
        return False
    if domain == "executive":
        return executive
    if domain in {"strategy", "shared_work"}:
        return True
    return bool(
        principal.roles
        & (
            {"DIVISION_LEAD", "DIVISION_MEMBER", "IT_ADMIN"}
            if domain == "it"
            else {"DIVISION_LEAD", "DIVISION_MEMBER"}
        )
    )


class BusinessReadAdapter:
    def __init__(self, tool_id: str, owner: Any) -> None:
        self.tool_id = tool_id
        self.owner = owner

    def validate_arguments(self, arguments: Mapping[str, Any]) -> None:
        _, operation, resource, _ = BUSINESS_TOOLS[self.tool_id]
        required = "resource_id" if operation == "detail" or operation.startswith("get_") else None
        if set(arguments) != ({required} if required else set()):
            raise ToolInputError("Only the registered explicit resource identifier is accepted.")
        if required and (
            not isinstance(arguments[required], str) or not 1 <= len(arguments[required]) <= 128
        ):
            raise ToolInputError(f"Invalid {resource} identifier.")

    async def execute(
        self, arguments: Mapping[str, Any], *, execution_context: Mapping[str, Any]
    ) -> Any:
        principal = principal_from_context(execution_context)
        if not business_tool_allowed(self.tool_id, principal):
            raise PermissionError("Business tool authority is unavailable.")
        domain, operation, resource, _ = BUSINESS_TOOLS[self.tool_id]
        method = getattr(self.owner, operation)
        if domain == "shared_work":
            if operation.startswith("get_"):
                result = await method(principal, arguments["resource_id"])
            else:
                filters: dict[str, Any] = {"status": None, "search": None}
                if operation == "list_tasks":
                    filters["priority"] = None
                if operation == "list_approvals":
                    filters["subject_type"] = None
                if operation == "list_findings":
                    filters.update(severity=None, source_type=None)
                result = (await method(principal, **filters))[:20]
        elif domain == "strategy" or domain == "executive":
            result = await method(principal)
        elif operation == "overview":
            result = await method(principal, executive="EXECUTIVE" in principal.roles)
        elif operation == "listing":
            result = await method(resource, principal, limit=20)
        else:
            result = await method(resource, principal, arguments["resource_id"])
        if is_dataclass(result) and not isinstance(result, type):
            result = asdict(result)
        # Preserve exact Decimal strings and unknown values; never manufacture zero.
        from decimal import Decimal

        encoded = jsonable_encoder(result, custom_encoder={Decimal: str})
        return {
            "data": encoded,
            "domain": domain,
            "tool_id": self.tool_id,
            "captured_at": datetime.now(UTC).isoformat(),
            "instruction_authority": False,
        }


def register_business_tools(registry: ToolRegistry, services: Any) -> None:
    for tool_id, (domain, _, _, permission) in BUSINESS_TOOLS.items():
        registry.register(
            ToolRegistration(
                tool_id=tool_id,
                required_permission=permission,
                required_scopes=frozenset(),
                adapter=BusinessReadAdapter(tool_id, getattr(services, f"{domain}_service")),
                timeout_seconds=10.0,
            )
        )
