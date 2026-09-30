"""Workspace-scoped CRUD over migration-owned domain tables."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from sqlalchemy import MetaData, Table, delete, exists, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import ColumnElement

from alos.identity import Principal
from alos.security.errors import PlatformError


@dataclass(frozen=True, slots=True)
class DomainResource:
    domain: str
    schema: str
    table: str
    primary_key: str
    workspace_link_table: str | None = None
    workspace_link_key: str | None = None
    global_admin: bool = False


_RESOURCE_NAMES: dict[str, tuple[str, ...]] = {
    "shared": ("projects", "tasks", "work_approvals", "work_reports", "work_findings"),
    "finance": (
        "bank_accounts",
        "bank_transactions",
        "receivables",
        "receivable_payments",
        "payables",
        "payable_payments",
        "budgets",
        "budget_lines",
        "reconciliations",
        "reconciliation_items",
        "tax_obligations",
        "tax_documents",
        "month_closes",
        "month_close_items",
    ),
    "hr": (
        "employees",
        "attendances",
        "leave_requests",
        "recruitments",
        "candidates",
        "interviews",
        "onboardings",
        "performance_reviews",
        "trainings",
        "training_enrollments",
        "successions",
        "succession_candidates",
        "grievances",
        "employment_contracts",
        "personnel_files",
    ),
    "legal": (
        "permits",
        "contracts",
        "land_documents",
        "due_diligences",
        "due_diligence_items",
        "cases",
        "claim_reviews",
        "expiries",
        "privacy_requests",
        "risks",
        "controls",
    ),
    "sales": (
        "customers",
        "leads",
        "opportunities",
        "site_visits",
        "bookings",
        "closings",
        "customer_followups",
        "customer_complaints",
        "pricings",
        "pricing_items",
        "collaterals",
    ),
    "marketing": ("campaigns", "channels", "attributions", "contents"),
    "property": (
        "property_units",
        "project_milestones",
        "construction_packages",
        "construction_updates",
        "quality_inspections",
        "quality_ncrs",
        "safety_incidents",
        "change_orders",
        "payment_certificates",
        "project_handovers",
        "land_pipeline",
    ),
    "it": (
        "systems",
        "integrations",
        "databases",
        "environments",
        "repositories",
        "cicd_pipelines",
        "ci_runs",
        "releases",
        "technical_debts",
        "service_monitors",
        "incidents",
        "security_findings",
        "backup_policies",
        "backup_runs",
        "restore_tests",
        "dr_plans",
    ),
    "genesis": (
        "agent_skills",
        "agent_models",
        "agent_tools",
        "uat_gates",
        "uat_results",
        "technical_decisions",
    ),
}

_RESOURCE_KEYS: dict[tuple[str, str], str] = {
    ("core", "projects"): "project_id",
    ("core", "tasks"): "task_id",
    ("core", "work_approvals"): "approval_id",
    ("core", "work_reports"): "report_id",
    ("core", "work_findings"): "finding_id",
    ("finance", "bank_accounts"): "bank_account_id",
    ("finance", "bank_transactions"): "transaction_id",
    ("finance", "receivables"): "receivable_id",
    ("finance", "receivable_payments"): "payment_id",
    ("finance", "payables"): "payable_id",
    ("finance", "payable_payments"): "payment_id",
    ("finance", "budgets"): "budget_id",
    ("finance", "budget_lines"): "budget_line_id",
    ("finance", "reconciliations"): "reconciliation_id",
    ("finance", "reconciliation_items"): "reconciliation_item_id",
    ("finance", "tax_obligations"): "tax_obligation_id",
    ("finance", "tax_documents"): "tax_document_id",
    ("finance", "month_closes"): "month_close_id",
    ("finance", "month_close_items"): "month_close_item_id",
    ("hr", "employees"): "employee_id",
    ("hr", "attendances"): "attendance_id",
    ("hr", "leave_requests"): "leave_request_id",
    ("hr", "recruitments"): "recruitment_id",
    ("hr", "candidates"): "candidate_id",
    ("hr", "interviews"): "interview_id",
    ("hr", "onboardings"): "onboarding_id",
    ("hr", "performance_reviews"): "performance_review_id",
    ("hr", "trainings"): "training_id",
    ("hr", "training_enrollments"): "training_enrollment_id",
    ("hr", "successions"): "succession_id",
    ("hr", "succession_candidates"): "succession_candidate_id",
    ("hr", "grievances"): "grievance_id",
    ("hr", "employment_contracts"): "employment_contract_id",
    ("hr", "personnel_files"): "personnel_file_id",
    ("legal", "permits"): "permit_id",
    ("legal", "contracts"): "contract_id",
    ("legal", "land_documents"): "land_document_id",
    ("legal", "due_diligences"): "due_diligence_id",
    ("legal", "due_diligence_items"): "due_diligence_item_id",
    ("legal", "cases"): "case_id",
    ("legal", "claim_reviews"): "claim_review_id",
    ("legal", "expiries"): "expiry_id",
    ("legal", "privacy_requests"): "privacy_request_id",
    ("legal", "risks"): "risk_id",
    ("legal", "controls"): "control_id",
    ("sales", "customers"): "customer_id",
    ("sales", "leads"): "lead_id",
    ("sales", "opportunities"): "opportunity_id",
    ("sales", "site_visits"): "site_visit_id",
    ("sales", "bookings"): "booking_id",
    ("sales", "closings"): "closing_id",
    ("sales", "customer_followups"): "followup_id",
    ("sales", "customer_complaints"): "complaint_id",
    ("sales", "pricings"): "pricing_id",
    ("sales", "pricing_items"): "pricing_item_id",
    ("sales", "collaterals"): "collateral_id",
    ("marketing", "campaigns"): "campaign_id",
    ("marketing", "channels"): "channel_id",
    ("marketing", "attributions"): "attribution_id",
    ("marketing", "contents"): "content_id",
    ("property", "property_units"): "property_unit_id",
    ("property", "project_milestones"): "milestone_id",
    ("property", "construction_packages"): "construction_package_id",
    ("property", "construction_updates"): "construction_update_id",
    ("property", "quality_inspections"): "inspection_id",
    ("property", "quality_ncrs"): "ncr_id",
    ("property", "safety_incidents"): "safety_incident_id",
    ("property", "change_orders"): "change_order_id",
    ("property", "payment_certificates"): "payment_certificate_id",
    ("property", "project_handovers"): "handover_id",
    ("property", "land_pipeline"): "land_pipeline_id",
    ("it", "systems"): "system_id",
    ("it", "integrations"): "integration_id",
    ("it", "databases"): "database_id",
    ("it", "environments"): "environment_id",
    ("it", "repositories"): "repository_id",
    ("it", "cicd_pipelines"): "pipeline_id",
    ("it", "ci_runs"): "ci_run_id",
    ("it", "releases"): "it_release_id",
    ("it", "technical_debts"): "technical_debt_id",
    ("it", "service_monitors"): "service_monitor_id",
    ("it", "incidents"): "incident_id",
    ("it", "security_findings"): "security_finding_id",
    ("it", "backup_policies"): "backup_policy_id",
    ("it", "backup_runs"): "backup_run_id",
    ("it", "restore_tests"): "restore_test_id",
    ("it", "dr_plans"): "dr_plan_id",
    ("genesis", "agent_skills"): "agent_skill_id",
    ("genesis", "agent_models"): "agent_model_id",
    ("genesis", "agent_tools"): "agent_tool_id",
    ("genesis", "uat_gates"): "uat_gate_id",
    ("genesis", "uat_results"): "uat_result_id",
    ("genesis", "technical_decisions"): "technical_decision_id",
}

_SHARED_LINKS = {
    "projects": ("project_workspaces", "project_id"),
    "tasks": ("task_workspaces", "task_id"),
    "work_approvals": ("work_approval_workspaces", "approval_id"),
    "work_reports": ("work_report_workspaces", "report_id"),
    "work_findings": ("work_finding_workspaces", "finding_id"),
}

GLOBAL_NAVIGATION_TABLES = frozenset({"navigation_groups", "navigation_items"})
PROTECTED_COLUMNS = frozenset({"tenant_id", "organization_id", "workspace_id"})
SERVER_ACTOR_COLUMNS = frozenset(
    {
        "created_by",
        "requested_by",
        "executed_by",
        "verified_by",
        "reconciled_by",
        "closed_by",
        "decided_by",
        "approved_by",
    }
)
READ_ONLY_COLUMNS = frozenset({"created_at", "updated_at", "created_by"}) | SERVER_ACTOR_COLUMNS


def _resource_catalog() -> dict[tuple[str, str], DomainResource]:
    catalog: dict[tuple[str, str], DomainResource] = {}
    for domain, tables in _RESOURCE_NAMES.items():
        schema = "core" if domain == "shared" else domain
        for table_name in tables:
            catalog[(schema, table_name)] = DomainResource(
                domain=domain,
                schema=schema,
                table=table_name,
                primary_key=_RESOURCE_KEYS[(schema, table_name)],
                workspace_link_table=(
                    _SHARED_LINKS[table_name][0] if table_name in _SHARED_LINKS else None
                ),
                workspace_link_key=(
                    _SHARED_LINKS[table_name][1] if table_name in _SHARED_LINKS else None
                ),
            )
    for table_name, primary_key in (
        ("navigation_groups", "navigation_group_id"),
        ("navigation_items", "navigation_item_id"),
    ):
        catalog[("core", table_name)] = DomainResource(
            domain="navigation",
            schema="core",
            table=table_name,
            primary_key=primary_key,
            global_admin=True,
        )
    return catalog


DOMAIN_RESOURCES = _resource_catalog()


class DomainCrudService:
    """CRUD service; all generated SQL uses a fixed migration-owned allowlist."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._metadata = MetaData()
        self._tables: dict[tuple[str, str], Table] = {}
        self._reflection_lock = asyncio.Lock()

    @staticmethod
    def resource(domain: str, table_name: str) -> DomainResource:
        schema = "core" if domain in {"shared", "navigation"} else domain
        resource = DOMAIN_RESOURCES.get((schema, table_name))
        if resource is None or resource.domain != domain:
            raise PlatformError(
                "DOMAIN_RESOURCE_NOT_FOUND", "Domain resource is not available.", status_code=404
            )
        return resource

    async def list_records(
        self,
        resource: DomainResource,
        principal: Principal,
        *,
        limit: int,
        offset: int,
        filters: dict[str, Any],
    ) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            table = await self._table(session, resource)
            predicates = await self._scope(session, resource, table, principal)
            for key, value in filters.items():
                if key in PROTECTED_COLUMNS:
                    raise PlatformError(
                        "INVALID_DOMAIN_FILTER",
                        "Authority-scoped fields cannot be supplied as filters.",
                        status_code=422,
                        details={"field": key},
                    )
                if key not in table.c:
                    raise PlatformError(
                        "INVALID_DOMAIN_FILTER",
                        "Unknown filter field.",
                        status_code=422,
                        details={"field": key},
                    )
                predicates.append(table.c[key] == self._coerce(table.c[key].type, value))
            statement = (
                select(table)
                .where(*predicates)
                .order_by(table.c[resource.primary_key])
                .limit(limit)
                .offset(offset)
            )
            rows = (await session.execute(statement)).mappings().all()
            return [self._project(resource, dict(row), principal) for row in rows]

    async def get_record(
        self, resource: DomainResource, principal: Principal, record_id: str
    ) -> dict[str, Any]:
        async with self._session_factory() as session:
            table = await self._table(session, resource)
            predicates = await self._scope(session, resource, table, principal)
            predicates.append(table.c[resource.primary_key] == record_id)
            row = (await session.execute(select(table).where(*predicates))).mappings().first()
            if row is None:
                raise self._not_found()
            return self._project(resource, dict(row), principal)

    async def create_record(
        self,
        resource: DomainResource,
        principal: Principal,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._reject_generic_shared_mutation(resource)
        async with self._session_factory() as session, session.begin():
            table = await self._table(session, resource)
            values = self._writable_values(resource, table, payload)
            values.update(self._authority_values(table, principal))
            self._audit_actor_values(values, table, principal)
            self._apply_timestamps(values, table, create=True)
            values.setdefault(resource.primary_key, uuid4().hex)
            await self._validate_foreign_keys(session, table, values, principal)
            await self._validate_actor_references(session, table, values, principal)
            await self._verify_workspace(session, principal)
            row = (
                (await session.execute(insert(table).values(**values).returning(table)))
                .mappings()
                .one()
            )
            record = self._project(resource, dict(row), principal)
            await self._link_shared_record(
                session, resource, record[resource.primary_key], principal
            )
            return record

    async def update_record(
        self,
        resource: DomainResource,
        principal: Principal,
        record_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._reject_generic_shared_mutation(resource)
        async with self._session_factory() as session, session.begin():
            table = await self._table(session, resource)
            values = self._writable_values(resource, table, payload, allow_primary_key=False)
            if not values:
                raise PlatformError(
                    "EMPTY_UPDATE", "Provide at least one editable field.", status_code=422
                )
            self._apply_timestamps(values, table, create=False)
            await self._validate_foreign_keys(session, table, values, principal)
            await self._validate_actor_references(session, table, values, principal)
            predicates = await self._scope(session, resource, table, principal)
            predicates.append(table.c[resource.primary_key] == record_id)
            row = (
                (
                    await session.execute(
                        update(table).where(*predicates).values(**values).returning(table)
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                raise self._not_found()
            return self._project(resource, dict(row), principal)

    async def delete_record(
        self, resource: DomainResource, principal: Principal, record_id: str
    ) -> None:
        self._reject_generic_shared_mutation(resource)
        async with self._session_factory() as session, session.begin():
            table = await self._table(session, resource)
            predicates = await self._scope(session, resource, table, principal)
            if resource.workspace_link_table:
                link = await self._link_table(session, resource)
                link_key = resource.workspace_link_key
                assert link is not None and link_key is not None
                result = await session.execute(
                    delete(link)
                    .where(
                        link.c[link_key] == record_id,
                        link.c.workspace_id == principal.workspace_id,
                    )
                    .returning(link.c[link_key])
                )
                if result.scalar_one_or_none() is None:
                    raise self._not_found()
                return
            predicates.append(table.c[resource.primary_key] == record_id)
            result = await session.execute(
                delete(table).where(*predicates).returning(table.c[resource.primary_key])
            )
            deleted_id = result.scalar_one_or_none()
            if deleted_id is None:
                raise self._not_found()

    async def list_workspace_navigation(self, principal: Principal) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            items = await self._table(
                session,
                DomainResource("navigation", "core", "navigation_items", "navigation_item_id"),
            )
            groups = await self._reflected_table(session, "core", "navigation_groups")
            links = await self._reflected_table(session, "core", "workspace_navigation")
            statement = (
                select(
                    items,
                    groups.c.code.label("group_code"),
                    groups.c.name.label("group_name"),
                    links.c.visible,
                    links.c.display_order.label("workspace_display_order"),
                )
                .join(links, links.c.navigation_item_id == items.c.navigation_item_id)
                .join(groups, groups.c.navigation_group_id == items.c.navigation_group_id)
                .where(
                    links.c.workspace_id == principal.workspace_id,
                    items.c.active.is_(True),
                    groups.c.active.is_(True),
                )
                .order_by(links.c.display_order, items.c.display_order)
            )
            return [dict(row) for row in (await session.execute(statement)).mappings().all()]

    async def set_workspace_navigation(
        self,
        principal: Principal,
        navigation_item_id: str,
        *,
        visible: bool,
        display_order: int,
    ) -> dict[str, Any]:
        async with self._session_factory() as session, session.begin():
            items = await self._reflected_table(session, "core", "navigation_items")
            links = await self._reflected_table(session, "core", "workspace_navigation")
            item = (
                await session.execute(
                    select(items.c.navigation_item_id).where(
                        items.c.navigation_item_id == navigation_item_id,
                        items.c.active.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if item is None:
                raise self._not_found()
            current = (
                (
                    await session.execute(
                        select(links).where(
                            links.c.workspace_id == principal.workspace_id,
                            links.c.navigation_item_id == navigation_item_id,
                        )
                    )
                )
                .mappings()
                .first()
            )
            if current is None:
                await session.execute(
                    insert(links).values(
                        workspace_id=principal.workspace_id,
                        navigation_item_id=navigation_item_id,
                        visible=visible,
                        display_order=display_order,
                    )
                )
            else:
                await session.execute(
                    update(links)
                    .where(
                        links.c.workspace_id == principal.workspace_id,
                        links.c.navigation_item_id == navigation_item_id,
                    )
                    .values(visible=visible, display_order=display_order)
                )
            return {
                "workspace_id": principal.workspace_id,
                "navigation_item_id": navigation_item_id,
                "visible": visible,
                "display_order": display_order,
            }

    async def remove_workspace_navigation(
        self, principal: Principal, navigation_item_id: str
    ) -> None:
        async with self._session_factory() as session, session.begin():
            links = await self._reflected_table(session, "core", "workspace_navigation")
            result = await session.execute(
                delete(links)
                .where(
                    links.c.workspace_id == principal.workspace_id,
                    links.c.navigation_item_id == navigation_item_id,
                )
                .returning(links.c.navigation_item_id)
            )
            if result.scalar_one_or_none() is None:
                raise self._not_found()

    async def _table(self, session: AsyncSession, resource: DomainResource) -> Table:
        key = (resource.schema, resource.table)
        cached = self._tables.get(key)
        if cached is not None:
            return cached
        async with self._reflection_lock:
            cached = self._tables.get(key)
            if cached is not None:
                return cached
            connection = await session.connection()
            try:
                table = await connection.run_sync(
                    lambda sync_connection: Table(
                        resource.table,
                        self._metadata,
                        schema=resource.schema,
                        autoload_with=sync_connection,
                    )
                )
            except SQLAlchemyError as exc:
                raise PlatformError(
                    "DOMAIN_SCHEMA_UNAVAILABLE",
                    "Domain storage is unavailable; apply the backend migrations first.",
                    status_code=503,
                ) from exc
            self._tables[key] = table
            return table

    async def _link_table(self, session: AsyncSession, resource: DomainResource) -> Table | None:
        if resource.workspace_link_table is None:
            return None
        link_table_name = resource.workspace_link_table
        key = ("core", link_table_name)
        cached = self._tables.get(key)
        if cached is not None:
            return cached
        connection = await session.connection()
        table = await connection.run_sync(
            lambda sync_connection: Table(
                link_table_name,
                self._metadata,
                schema="core",
                autoload_with=sync_connection,
            )
        )
        self._tables[key] = table
        return table

    async def _link_shared_record(
        self,
        session: AsyncSession,
        resource: DomainResource,
        record_id: Any,
        principal: Principal,
    ) -> None:
        link = await self._link_table(session, resource)
        link_key = resource.workspace_link_key
        if link is None or link_key is None:
            return
        await session.execute(
            insert(link).values(
                **{
                    link_key: record_id,
                    "workspace_id": principal.workspace_id,
                }
            )
        )

    async def _validate_foreign_keys(
        self,
        session: AsyncSession,
        table: Table,
        values: dict[str, Any],
        principal: Principal,
    ) -> None:
        for column_name, value in values.items():
            if value is None:
                continue
            column = table.c[column_name]
            for foreign_key in column.foreign_keys:
                target = await self._reflected_table(
                    session,
                    foreign_key.column.table.schema or "core",
                    foreign_key.column.table.name,
                )
                target_column = foreign_key.column.name
                predicates: list[ColumnElement[bool]] = [target.c[target_column] == value]
                if target.name == "workspaces":
                    predicates.extend(
                        [
                            target.c.tenant_id == principal.tenant_id,
                            target.c.organization_id == principal.organization_id,
                            target.c.workspace_id == principal.workspace_id,
                        ]
                    )
                elif {"tenant_id", "organization_id", "workspace_id"}.issubset(target.c.keys()):
                    predicates.extend(
                        [
                            target.c.tenant_id == principal.tenant_id,
                            target.c.organization_id == principal.organization_id,
                            target.c.workspace_id == principal.workspace_id,
                        ]
                    )
                elif {"tenant_id", "organization_id"}.issubset(target.c.keys()):
                    predicates.extend(
                        [
                            target.c.tenant_id == principal.tenant_id,
                            target.c.organization_id == principal.organization_id,
                        ]
                    )
                    shared_link = _SHARED_LINKS.get(target.name)
                    if target.schema == "core" and shared_link:
                        link_resource = DomainResource(
                            domain="shared",
                            schema="core",
                            table=target.name,
                            primary_key=target_column,
                            workspace_link_table=shared_link[0],
                            workspace_link_key=shared_link[1],
                        )
                        link = await self._link_table(session, link_resource)
                        assert link is not None
                        predicates.append(
                            exists(
                                select(1)
                                .select_from(link)
                                .where(
                                    link.c[shared_link[1]] == target.c[target_column],
                                    link.c.workspace_id == principal.workspace_id,
                                )
                            )
                        )
                if (
                    await session.execute(
                        select(target.c[target_column]).where(*predicates).limit(1)
                    )
                ).first() is None:
                    raise PlatformError(
                        "DOMAIN_REFERENCE_NOT_FOUND",
                        f"Referenced {column_name} is unavailable in the active workspace.",
                        status_code=404,
                    )

    async def _validate_actor_references(
        self,
        session: AsyncSession,
        table: Table,
        values: dict[str, Any],
        principal: Principal,
    ) -> None:
        actor_fields = {
            name for name in values if name.endswith("_actor_id") or name == "assigned_to"
        }
        if not actor_fields:
            return
        actors = await self._reflected_table(session, "core", "actors")
        memberships = await self._reflected_table(session, "core", "workspace_memberships")
        for name in actor_fields:
            actor_id = values[name]
            if actor_id is None:
                continue
            accessible_actor = await session.execute(
                select(actors.c.actor_id)
                .join(memberships, memberships.c.actor_id == actors.c.actor_id)
                .where(
                    actors.c.actor_id == actor_id,
                    actors.c.tenant_id == principal.tenant_id,
                    actors.c.organization_id == principal.organization_id,
                    actors.c.active.is_(True),
                    memberships.c.workspace_id == principal.workspace_id,
                    memberships.c.active.is_(True),
                    memberships.c.revoked_at.is_(None),
                )
                .limit(1)
            )
            if accessible_actor.scalar_one_or_none() is None:
                raise PlatformError(
                    "DOMAIN_REFERENCE_NOT_FOUND",
                    f"Referenced {name} is unavailable in the active workspace.",
                    status_code=404,
                )

    async def _reflected_table(self, session: AsyncSession, schema: str, table_name: str) -> Table:
        key = (schema, table_name)
        table = self._tables.get(key)
        if table is not None:
            return table
        connection = await session.connection()
        table = await connection.run_sync(
            lambda sync_connection: Table(
                table_name, self._metadata, schema=schema, autoload_with=sync_connection
            )
        )
        self._tables[key] = table
        return table

    async def _verify_workspace(self, session: AsyncSession, principal: Principal) -> None:
        workspaces = await self._reflected_table(session, "core", "workspaces")
        exists_in_org = await session.execute(
            select(workspaces.c.workspace_id).where(
                workspaces.c.workspace_id == principal.workspace_id,
                workspaces.c.tenant_id == principal.tenant_id,
                workspaces.c.organization_id == principal.organization_id,
                workspaces.c.active.is_(True),
            )
        )
        if exists_in_org.scalar_one_or_none() is None:
            raise PlatformError(
                "WORKSPACE_ACCESS_DENIED", "Active workspace is unavailable.", status_code=403
            )

    async def _scope(
        self,
        session: AsyncSession,
        resource: DomainResource,
        table: Table,
        principal: Principal,
    ) -> list[ColumnElement[bool]]:
        if resource.global_admin:
            return []
        if resource.workspace_link_table:
            link = await self._link_table(session, resource)
            link_key = resource.workspace_link_key
            assert link is not None and link_key is not None
            return [
                table.c.tenant_id == principal.tenant_id,
                table.c.organization_id == principal.organization_id,
                exists(
                    select(1)
                    .select_from(link)
                    .where(
                        link.c[link_key] == table.c[resource.primary_key],
                        link.c.workspace_id == principal.workspace_id,
                    )
                ),
            ]
        return [
            table.c.tenant_id == principal.tenant_id,
            table.c.organization_id == principal.organization_id,
            table.c.workspace_id == principal.workspace_id,
        ]

    def _writable_values(
        self,
        resource: DomainResource,
        table: Table,
        payload: dict[str, Any],
        *,
        allow_primary_key: bool = True,
    ) -> dict[str, Any]:
        protected = set(PROTECTED_COLUMNS | READ_ONLY_COLUMNS)
        if not allow_primary_key:
            protected.add(resource.primary_key)
        unknown = set(payload) - set(table.c.keys())
        forbidden = set(payload) & protected
        if unknown or forbidden:
            fields = sorted(unknown | forbidden)
            raise PlatformError(
                "INVALID_DOMAIN_FIELDS",
                "Request contains unknown or authority-controlled fields.",
                status_code=422,
                details={"fields": fields},
            )
        return {key: self._coerce(table.c[key].type, value) for key, value in payload.items()}

    @staticmethod
    def _reject_generic_shared_mutation(resource: DomainResource) -> None:
        if resource.domain == "shared" and resource.table == "work_approvals":
            raise PlatformError(
                "APPROVAL_LIFECYCLE_REQUIRES_DEDICATED_API",
                "Approval changes require a dedicated authorization boundary.",
                status_code=409,
            )
        if resource.domain == "shared" and resource.table in {"projects", "tasks"}:
            raise PlatformError(
                "WORK_MUTATION_REQUIRES_DEDICATED_API",
                "Project and task changes require their dedicated authorization boundary.",
                status_code=409,
            )

    @staticmethod
    def _authority_values(table: Table, principal: Principal) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for column, value in (
            ("tenant_id", principal.tenant_id),
            ("organization_id", principal.organization_id),
            ("workspace_id", principal.workspace_id),
        ):
            if column in table.c:
                values[column] = value
        return values

    @staticmethod
    def _audit_actor_values(values: dict[str, Any], table: Table, principal: Principal) -> None:
        for column in {"created_by", "requested_by", "executed_by"}:
            if column in table.c:
                values[column] = principal.actor_id
        if "owner_actor_id" in table.c and values.get("owner_actor_id") is None:
            values["owner_actor_id"] = principal.actor_id

    @staticmethod
    def _apply_timestamps(values: dict[str, Any], table: Table, *, create: bool) -> None:
        now = datetime.now(UTC)
        for column in ("created_at", "updated_at"):
            if column in table.c and (create or column == "updated_at"):
                values[column] = now

    def _project(
        self, resource: DomainResource, row: dict[str, Any], principal: Principal
    ) -> dict[str, Any]:
        if resource.workspace_link_table:
            row["workspace_id"] = principal.workspace_id
        return row

    @staticmethod
    def _coerce(column_type: Any, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(column_type, type):
            return value
        type_name = type(column_type).__name__.lower()
        try:
            if type_name == "date" and isinstance(value, str):
                return date.fromisoformat(value)
            if type_name == "datetime" and isinstance(value, str):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            if type_name in {"numeric", "decimal"} and not isinstance(value, Decimal):
                return Decimal(str(value))
            if type_name in {"integer", "smallinteger", "biginteger"}:
                return int(value)
            if type_name == "float":
                return float(value)
            if type_name == "boolean" and isinstance(value, str):
                normalized = value.strip().lower()
                if normalized in {"true", "1", "yes"}:
                    return True
                if normalized in {"false", "0", "no"}:
                    return False
                raise ValueError("must be a boolean")
            if type_name == "boolean" and not isinstance(value, bool):
                raise ValueError("must be a JSON boolean")
        except (ValueError, InvalidOperation) as exc:
            raise PlatformError(
                "INVALID_DOMAIN_VALUE", "A field value has an invalid type.", status_code=422
            ) from exc
        return value

    @staticmethod
    def _not_found() -> PlatformError:
        return PlatformError(
            "DOMAIN_RECORD_NOT_FOUND", "Domain record was not found.", status_code=404
        )
