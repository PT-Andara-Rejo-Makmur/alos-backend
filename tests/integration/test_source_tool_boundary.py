from pathlib import Path

import pytest

from alos.audit import InMemoryAuditRepository
from alos.authorization import AuthorizationPolicy
from alos.contracts import CanonicalContractCatalog
from alos.evidence import EvidenceRegistry
from alos.identity import Principal
from alos.sources import KnowledgeAccessContext, SourceRegistration, SourceRegistry
from alos.tools.adapters import SourceContextSearchAdapter
from alos.tools.contracts import JsonSchemaToolContractValidator
from alos.tools.executor import InMemoryToolAuditSink, ToolExecutor
from alos.tools.registry import ToolRegistration, ToolRegistry

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


@pytest.mark.asyncio
async def test_genesis_context_request_runs_only_through_backend_tool_executor() -> None:
    catalog = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    sources = SourceRegistry(
        contracts=catalog,
        evidence=EvidenceRegistry(catalog),
        audit=audit,
    )
    authority = KnowledgeAccessContext(
        tenant_id="tenant_knowledge_001",
        organization_id="org_knowledge_001",
        workspace_id="workspace_knowledge_001",
        actor_id="actor_knowledge_001",
        correlation_id="corr_knowledge_tool_001",
        scope_refs=frozenset({"scope.sources.write", "scope.sources.verify", "scope.sources.read"}),
        data_classification="INTERNAL",
    )
    source = SourceRegistration(
        source_id="source_policy_001",
        source_version="1.0.0",
        title="Policy",
        storage_uri="urn:alos:source:policy:1",
        content="The authoritative retention period is seven years.",
    )
    await sources.register(source, access=authority)
    await sources.verify(source.source_id, source.source_version, access=authority)

    registry = ToolRegistry()
    registry.register(
        ToolRegistration(
            tool_id="source.search_context",
            required_permission="sources.read",
            required_scopes=frozenset({"scope.sources.read"}),
            adapter=SourceContextSearchAdapter(sources),
        )
    )
    tool_audit = InMemoryToolAuditSink()
    executor = ToolExecutor(
        contract_validator=JsonSchemaToolContractValidator(CONTRACTS_ROOT),
        authorization=AuthorizationPolicy(),
        registry=registry,
        audit_sink=tool_audit,
        production=False,
    )
    request = {
        "tool_call_id": "toolcall_context_001",
        "run_id": "run_context_001",
        "tool_id": "source.search_context",
        "execution_context": {
            "tenant_id": authority.tenant_id,
            "organization_id": authority.organization_id,
            "workspace_id": authority.workspace_id,
            "actor_id": authority.actor_id,
            "authority_context": {"role": "GENESIS", "authority_level": "SYSTEM"},
            "permission_refs": ["sources.read"],
            "scope_refs": ["scope.sources.read"],
            "data_classification": "INTERNAL",
            "correlation_id": authority.correlation_id,
        },
        "arguments": {"query": "retention"},
    }
    principal = Principal(
        actor_id=authority.actor_id,
        tenant_id=authority.tenant_id,
        organization_id=authority.organization_id,
        workspace_id=authority.workspace_id,
        permissions=frozenset({"sources.read"}),
        scopes=frozenset({"scope.sources.read"}),
    )

    outcome = await executor.execute(request, principal=principal)

    assert outcome.result["status"] == "SUCCESS"
    context = outcome.result["output"]
    assert context["correlation_id"] == authority.correlation_id
    assert context["items"][0]["source_id"] == source.source_id
    assert [record.outcome for record in tool_audit.records] == ["REQUESTED", "SUCCESS"]
