from alos.context.bundle import ContextBundleBuilder, ContextBuildRequest
from alos.identity import DataScope, Principal
from alos.sources.requirement import SourceRequirementBuilder


def test_context_bundle_builder_includes_division_and_project_scope() -> None:
    principal = Principal(
        actor_id="actor_001",
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        permissions=frozenset({"capability.read", "tool.execute"}),
        scopes=frozenset({"scope.workspace.001", "scope.division.01"}),
        roles=frozenset({"DIVISION_LEAD"}),
        data_scope=DataScope.DIVISION,
        division_id="division_01",
        project_id="project_01",
        active=True,
    )

    bundle = ContextBundleBuilder().build(
        principal,
        request=ContextBuildRequest(
            goal="Resolve onboarding risk",
            capability_ids=("capability.read",),
            tool_ids=("diagnostic.echo",),
            budget_hint=250,
            token_hint=4096,
        ),
        correlation_id="corr-h03-1",
    )

    assert bundle.status == "ACTIVE"
    assert bundle.division_id == "division_01"
    assert bundle.project_id == "project_01"
    assert "capability.read" in bundle.allowed_capabilities
    assert "diagnostic.echo" in bundle.allowed_tools
    assert bundle.budget == 250
    assert bundle.token_limit == 4096


def test_source_requirement_builder_rejects_untrusted_external_scope_expansion() -> None:
    principal = Principal(
        actor_id="actor_002",
        tenant_id="tenant_001",
        organization_id="org_001",
        workspace_id="workspace_001",
        permissions=frozenset({"sources.read"}),
        scopes=frozenset({"scope.workspace.001"}),
        roles=frozenset({"DIVISION_MEMBER"}),
        data_scope=DataScope.PROJECT,
        division_id="division_01",
        project_id="project_01",
        active=True,
    )

    requirement = SourceRequirementBuilder().build(
        principal,
        source_classification="EXTERNAL",
        requested_scope="scope.project.01",
    )

    assert requirement.is_valid is False
    assert requirement.allows_external_sources is False
    assert "scope.project.01" in requirement.requested_scope
