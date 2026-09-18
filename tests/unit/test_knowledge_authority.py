from datetime import UTC, datetime
from pathlib import Path

import pytest

from alos.audit import InMemoryAuditRepository
from alos.contracts import CanonicalContractCatalog
from alos.documents import DocumentRegistry, DocumentRegistryError, DocumentVersion
from alos.documents.models import DataClassification
from alos.evidence import EvidenceConflictError, EvidenceRegistry
from alos.sources import (
    KnowledgeAccessContext,
    SourceRegistration,
    SourceRegistry,
    SourceRegistryError,
)

CONTRACTS_ROOT = Path(__file__).resolve().parents[3] / "alos-contracts"


def access(
    *scopes: str,
    classification: DataClassification = "INTERNAL",
) -> KnowledgeAccessContext:
    return KnowledgeAccessContext(
        tenant_id="tenant_knowledge_001",
        organization_id="org_knowledge_001",
        workspace_id="workspace_knowledge_001",
        actor_id="actor_knowledge_001",
        correlation_id="corr_knowledge_001",
        scope_refs=frozenset(scopes),
        data_classification=classification,
    )


def source_registry() -> tuple[SourceRegistry, InMemoryAuditRepository]:
    contracts = CanonicalContractCatalog(CONTRACTS_ROOT)
    audit = InMemoryAuditRepository()
    return (
        SourceRegistry(
            contracts=contracts,
            evidence=EvidenceRegistry(contracts),
            audit=audit,
        ),
        audit,
    )


@pytest.mark.asyncio
async def test_source_versions_are_immutable_verified_and_tenant_scoped() -> None:
    registry, audit = source_registry()
    writer = access("scope.sources.write", "scope.sources.verify", "scope.sources.read")
    command = SourceRegistration(
        source_id="source_policy_001",
        source_version="1.0.0",
        title="Policy",
        source_type="TEXT",
        data_classification="INTERNAL",
        storage_uri="urn:alos:source:policy:1",
        content="Retention: seven years\nOwner: Finance",
    )
    await registry.register(command, access=writer)
    await registry.verify(command.source_id, command.source_version, access=writer)

    context = await registry.search_context("retention", access=writer)
    assert context["tenant_id"] == writer.tenant_id
    assert context["correlation_id"] == writer.correlation_id
    assert context["items"][0]["source_version"] == "1.0.0"
    assert context["items"][0]["evidence_id"] == context["evidence_refs"][0]["evidence_id"]

    changed = command.model_copy(update={"content": "Retention: one year"})
    with pytest.raises(SourceRegistryError, match="immutable"):
        await registry.register(changed, access=writer)

    other_workspace = writer.model_copy(update={"workspace_id": "workspace_other_001"})
    isolated = await registry.search_context("retention", access=other_workspace)
    assert isolated["workspace_id"] == "workspace_other_001"
    assert isolated["items"] == []
    outcomes = [event.outcome for event in audit.list_events(tenant_id=writer.tenant_id)]
    assert "VERIFIED" in outcomes
    assert "SUCCESS" in outcomes


@pytest.mark.asyncio
async def test_source_retrieval_honors_classification_and_character_budget() -> None:
    registry, _audit = source_registry()
    privileged = access(
        "scope.sources.write",
        "scope.sources.verify",
        "scope.sources.read",
        classification="CONFIDENTIAL",
    )
    command = SourceRegistration(
        source_id="source_confidential_001",
        source_version="1.0.0",
        title="Confidential note",
        data_classification="CONFIDENTIAL",
        storage_uri="urn:alos:source:confidential:1",
        content="forecast " * 300,
    )
    await registry.register(command, access=privileged)
    await registry.verify(command.source_id, command.source_version, access=privileged)

    internal = privileged.model_copy(update={"data_classification": "INTERNAL"})
    assert (await registry.search_context("forecast", access=internal))["items"] == []
    bounded = await registry.search_context(
        "forecast",
        access=privileged,
        max_characters=80,
    )
    assert sum(len(str(item["value"])) for item in bounded["items"]) <= 80


@pytest.mark.asyncio
async def test_document_metadata_and_version_are_authoritative_and_immutable() -> None:
    audit = InMemoryAuditRepository()
    registry = DocumentRegistry(audit)
    writer = access("scope.documents.write", "scope.documents.read")
    document = await registry.register(
        document_id="document_policy_001",
        title="Policy",
        category="GOVERNANCE",
        data_classification="INTERNAL",
        access=writer,
    )
    version = DocumentVersion(
        document_id=document.document_id,
        version="1.0.0",
        source_id="source_policy_001",
        source_version="1.0.0",
        storage_uri="urn:alos:document:policy:1",
        content_hash="sha256:" + "a" * 64,
        created_by=writer.actor_id,
        created_at=datetime.now(UTC),
    )
    await registry.add_version(version, access=writer)
    loaded, versions = registry.get(document.document_id, access=writer)
    assert loaded.tenant_id == writer.tenant_id
    assert versions == (version,)

    with pytest.raises(DocumentRegistryError, match="immutable"):
        await registry.add_version(
            version.model_copy(update={"content_hash": "sha256:" + "b" * 64}),
            access=writer,
        )


def test_evidence_id_cannot_be_rebound_to_different_content() -> None:
    registry = EvidenceRegistry(CanonicalContractCatalog(CONTRACTS_ROOT))
    payload = {
        "tenant_id": "tenant_knowledge_001",
        "workspace_id": "workspace_knowledge_001",
        "evidence_id": "evidence_knowledge_001",
        "source_id": "source_policy_001",
        "uri": "urn:alos:evidence:policy:1",
        "captured_at": "2026-09-18T00:00:00Z",
        "content_hash": "sha256:" + "a" * 64,
    }
    registry.register(payload)
    with pytest.raises(EvidenceConflictError, match="immutable"):
        registry.register({**payload, "content_hash": "sha256:" + "b" * 64})
