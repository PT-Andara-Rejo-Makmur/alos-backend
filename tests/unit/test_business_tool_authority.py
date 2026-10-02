"""Curated tool matrix: adapters never bypass the authoritative executor."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from alos.authorization import AuthorizationPolicy
from alos.context.bundle import ContextBuildRequest, ContextBundleBuilder
from alos.identity import DataScope, Principal
from alos.tools.business.catalog import BUSINESS_TOOLS, BusinessReadAdapter
from alos.tools.contracts import JsonSchemaToolContractValidator
from alos.tools.executor.service import InMemoryToolAuditSink, ToolExecutor
from alos.tools.registry import ToolLifecycleState, ToolRegistration, ToolRegistry

ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_id", BUSINESS_TOOLS)
@pytest.mark.parametrize(
    "case",
    (
        "success",
        "arguments",
        "permission",
        "scope",
        "workspace",
        "organization",
        "tenant",
        "actor",
        "inactive",
        "kill_switch",
        "correlation",
        "run_allowlist",
        "classification",
        "data_scope",
        "division",
        "project",
        "role",
    ),
)
async def test_business_tool_matrix(tool_id: str, case: str) -> None:
    domain, operation, _, permission = BUSINESS_TOOLS[tool_id]
    principal = Principal(
        actor_id="actor_tool",
        tenant_id="tenant_tool",
        organization_id="org_tool",
        workspace_id="workspace_tool",
        roles=frozenset({"EXECUTIVE"} if domain == "executive" else {"DIVISION_MEMBER"}),
        permissions=frozenset({permission}),
        scopes=frozenset({"scope.business"}),
        data_scope=DataScope.COMPANY if domain == "executive" else DataScope.WORKSPACE,
    )
    owner = SimpleNamespace(
        **{
            operation: AsyncMock(
                return_value=[] if operation.startswith("list_") else {"unknown": None}
            )
        }
    )
    registry = ToolRegistry()
    registration = ToolRegistration(
        tool_id=tool_id,
        required_permission=permission,
        required_scopes=frozenset({"scope.business"}),
        adapter=BusinessReadAdapter(tool_id, owner),
    )
    if case == "inactive":
        registration = replace(registration, lifecycle_state=ToolLifecycleState.SUSPENDED)
    if case == "kill_switch":
        registration = replace(registration, kill_switch_active=True)
    registry.register(registration)
    context = {
        key: getattr(principal, key)
        for key in ("tenant_id", "organization_id", "workspace_id", "actor_id")
    }
    context.update(
        authority_context={
            "role": next(iter(principal.roles)),
            "role_refs": list(principal.roles),
            "authority_level": "REQUESTER",
        },
        permission_refs=list(principal.permissions),
        scope_refs=list(principal.scopes),
        data_classification="INTERNAL",
        correlation_id="corr_tool",
        data_scope=principal.data_scope.value,
    )
    arguments = (
        {"resource_id": "resource_tool"}
        if operation == "detail" or operation.startswith("get_")
        else {}
    )
    if case in {"workspace", "organization", "tenant", "actor"}:
        context[f"{case}_id"] = "foreign_context"
    if case == "permission":
        principal = replace(principal, permissions=frozenset())
    if case == "scope":
        principal = replace(principal, scopes=frozenset({"scope.foreign"}))
    if case == "arguments":
        arguments["sql"] = "SELECT forbidden"
    if case == "classification":
        context["data_classification"] = "RESTRICTED"
    if case == "data_scope":
        context["data_scope"] = "PROJECT"
    if case in {"division", "project"}:
        context[f"{case}_id"] = "foreign_scope"
    if case == "role":
        context["authority_context"]["role_refs"] = ["IT_ADMIN"]
    executor = ToolExecutor(
        contract_validator=JsonSchemaToolContractValidator(ROOT),
        authorization=AuthorizationPolicy(),
        registry=registry,
        audit_sink=InMemoryToolAuditSink(),
        production=False,
    )
    outcome = await executor.execute(
        {
            "tool_call_id": "toolcall_test",
            "run_id": "run_tool",
            "tool_id": tool_id,
            "execution_context": context,
            "arguments": arguments,
        },
        principal=principal,
        transport_correlation_id="corr_foreign" if case == "correlation" else "corr_tool",
        authorized_tool_ids=frozenset() if case == "run_allowlist" else frozenset({tool_id}),
    )
    expected = "SUCCESS" if case == "success" else "REJECTED" if case == "arguments" else "DENIED"
    assert outcome.result["status"] == expected
    if case == "success":
        assert outcome.result["evidence_refs"][0]["instruction_authority"] is False
        assert outcome.result["source_refs"]
        getattr(owner, operation).assert_awaited_once()
    else:
        getattr(owner, operation).assert_not_awaited()


def test_context_cannot_expand_tools_or_capabilities() -> None:
    principal = Principal(
        actor_id="actor_context",
        tenant_id="tenant_context",
        organization_id="org_context",
        workspace_id="workspace_context",
        roles=frozenset({"DIVISION_MEMBER"}),
        permissions=frozenset({"sales.read"}),
        scopes=frozenset({"scope.business"}),
    )
    registry = ToolRegistry()
    registry.register(
        ToolRegistration(
            tool_id="sales.lead.list",
            required_permission="sales.read",
            required_scopes=frozenset(),
            adapter=BusinessReadAdapter("sales.lead.list", None),
        )
    )
    context = ContextBundleBuilder(registry).build(
        principal,
        request=ContextBuildRequest(
            capability_ids=("business.question_answering", "admin"),
            tool_ids=("sales.lead.list", "finance.overview.read", "admin"),
            token_hint=999999,
        ),
        correlation_id="corr_context",
    )
    assert context.allowed_tools == ("sales.lead.list",)
    assert context.allowed_capabilities == ("business.question_answering",)
    assert context.token_limit == 12000
