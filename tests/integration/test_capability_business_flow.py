"""Business validation, actual GENESIS Factory/review and existing governed activation."""

from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_ara_business_flow import GenesisSettings, genesis_app
from test_business_domains import Context
from test_business_domains import context as migrated_context
from test_business_processes import action, submit

from alos.dependencies import get_genesis_client
from alos.governance.gates.assurance import (
    AssuranceEvaluator,
    AutomatedAssuranceReport,
    ExpectedBehavior,
    ObservedBehavior,
    TestCategory,
)
from alos.governance.materiality import Materiality
from alos.integrations.genesis import GenesisClient
from alos.persistence.models import ReviewPackageRecord
from alos.registry import DecisionAuthority
from alos.reviews.packages import ReviewPackageReference

context = migrated_context
pytestmark = pytest.mark.asyncio(loop_scope="module")


async def test_business_need_factory_review_test_it_release_and_active(context: Context) -> None:
    app = context.app
    token = SecretStr("business-factory-test-token")
    runtime = genesis_app(
        GenesisSettings(
            _env_file=None,
            APP_ENV="test",
            ALOS_BACKEND_BASE_URL="http://backend.test",
            ALOS_INTERNAL_TOKEN=token,
            ALOS_CONTRACTS_PATH=app.state.settings.ALOS_CONTRACTS_PATH,
            ENABLE_TEST_RUNTIME=True,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=runtime), base_url="http://genesis.test"
    ) as intelligence:
        app.dependency_overrides[get_genesis_client] = lambda: GenesisClient(
            base_url="http://genesis.test", internal_token=token, client=intelligence
        )
        path = "/api/v1/business/capability-requests"
        need = {
            "need": "Create an agent assistant to coordinate operational evidence",
            "goal": "Provide an evidence based summary for human review",
            "business_context": "Company operations workspace",
        }
        forged = await context.client.post(
            path, headers=context.headers["member"], json={**need, "model": "uncontrolled"}
        )
        assert forged.status_code == 422
        created = await context.client.post(path, headers=context.headers["member"], json=need)
        assert created.status_code == 201, created.text
        request_id = created.json()["request_id"]
        hidden = await context.client.get(
            path + "/" + request_id, headers=context.headers["workspace"]
        )
        assert hidden.status_code == 404
        policy = await context.client.post(
            "/api/v1/processes/policies",
            headers=context.headers["it"],
            json={
                "business_type": "CAPABILITY_REQUEST",
                "owning_workspace_id": "workspace_business",
                "routes": {"owner": "workspace_business"},
                "rules": {},
                "reason": "Kepala divisi memeriksa kebutuhan sebelum resolusi",
            },
        )
        assert policy.status_code == 200, policy.text
        denied = await context.client.post(
            f"{path}/{request_id}/resolve",
            headers=context.headers["member"],
            json={"reason": "Anggota tidak melewati pemeriksaan"},
        )
        assert denied.status_code == 403
        premature = await context.client.post(
            f"{path}/{request_id}/resolve",
            headers=context.headers["lead"],
            json={"reason": "Belum selesai"},
        )
        assert premature.status_code == 409
        process = await submit(context, "CAPABILITY_REQUEST", request_id)
        process = await action(context, process, "lead", 0)
        assert process["status"] == "COMPLETED"
        resolved = await context.client.post(
            f"{path}/{request_id}/resolve",
            headers=context.headers["lead"],
            json={"reason": "Kebutuhan dan tujuan telah diperiksa"},
        )
        assert resolved.status_code == 200, resolved.text
        result = resolved.json()["factory_result"]
        assert result["decision"] == "CREATE" and result["registry_result"]["state"] == "DRAFT"
        draft = result["agent_draft"]
        assert draft is not None and draft["lifecycle_state"] == "DRAFT"
        repeated = await context.client.post(
            f"{path}/{request_id}/resolve",
            headers=context.headers["lead"],
            json={"reason": "Baca hasil yang sama"},
        )
        assert repeated.json()["factory_result"] == result
        reviewed = await context.client.post(
            f"{path}/{request_id}/review",
            headers=context.headers["lead"],
            json={"reason": "Tinjauan teknis melalui GENESIS existing"},
        )
        assert reviewed.status_code == 200, reviewed.text
        package = reviewed.json()
        assert "it_decision" not in package and "director_decision" not in package
        review_id = package["identity"]["review_id"]
        async with app.state.database.session_factory() as session:
            review = await session.get(ReviewPackageRecord, review_id)
            assert review is not None
        authority = app.state.release_authority
        release_id = "release_" + uuid4().hex
        maker = created.json()["requested_by"]
        lead = (
            await app.state.auth_service.whoami(
                context.headers["lead"]["Authorization"].split(" ", 1)[1]
            )
        )["actor"]["actor_id"]
        await authority.create(
            release_id=release_id,
            review_id=review_id,
            tenant_id="tenant_business",
            organization_id="org_business",
            workspace_id="workspace_business",
            subject_id=draft["agent_id"],
            subject_version=draft["version"],
            materiality=Materiality.NON_MATERIAL,
            actor_id=maker,
            correlation_id="corr_business_release",
        )
        premature_release = await context.client.post(
            f"/api/v1/releases/{release_id}/actions/release", headers=context.headers["it"], json={}
        )
        assert premature_release.status_code == 409
        await authority.mark_implemented(
            release_id, actor_id=maker, correlation_id="corr_business_implementation"
        )
        observations = (
            ("factory_create", TestCategory.POSITIVE, "200", str(resolved.status_code)),
            ("scope_boundary", TestCategory.SECURITY, "404", str(hidden.status_code)),
            ("model_injection", TestCategory.NEGATIVE, "422", str(forged.status_code)),
        )
        report = AutomatedAssuranceReport(
            checks=tuple(
                AssuranceEvaluator().evaluate(
                    test_id=name,
                    category=category,
                    expected=ExpectedBehavior(status=expected),
                    observed=ObservedBehavior(status=observed),
                )
                for name, category, expected, observed in observations
            ),
            required_categories=frozenset(
                {TestCategory.POSITIVE, TestCategory.NEGATIVE, TestCategory.SECURITY}
            ),
        )
        await authority.record_automated_assurance(
            release_id, report, actor_id=lead, correlation_id="corr_business_assurance"
        )
        await authority.record_ai_review_package(
            release_id,
            ReviewPackageReference(
                review_id=review.review_id,
                tenant_id=review.tenant_id,
                workspace_id=review.workspace_id,
                subject_id=review.subject_id,
                subject_version=review.subject_version,
                contract_version=review.contract_version,
                evidence_uri=review.evidence_uri,
                recorded_at=review.recorded_at,
            ),
            actor_id=lead,
            correlation_id="corr_business_ai_review",
        )
        await authority.submit_for_it(
            release_id, actor_id=lead, correlation_id="corr_business_submit"
        )
        member_decision = await context.client.post(
            f"/api/v1/releases/{release_id}/actions/it-decision",
            headers=context.headers["member"],
            json={
                "decision_id": "decision_" + uuid4().hex,
                "outcome": "APPROVED",
                "rationale": "Tidak berwenang",
            },
        )
        assert member_decision.status_code == 403
        decision = await context.client.post(
            f"/api/v1/releases/{release_id}/actions/it-decision",
            headers=context.headers["it"],
            json={
                "decision_id": "decision_" + uuid4().hex,
                "outcome": "APPROVED",
                "rationale": "Review dan pengujian diperiksa IT",
                "decided_at": datetime.now(UTC).isoformat(),
            },
        )
        assert decision.status_code == 200, decision.text
        for command in ("release", "activate"):
            response = await context.client.post(
                f"/api/v1/releases/{release_id}/actions/{command}",
                headers=context.headers["it"],
                json={},
            )
            assert response.status_code == 200, response.text
        projection = await context.client.get(
            f"{path}/{request_id}", headers=context.headers["member"]
        )
        active = next(
            item
            for item in projection.json()["governance"]
            if item["subject_id"] == draft["agent_id"]
        )
        assert active["registry_state"] == active["release_state"] == "ACTIVE"
        assert active["release_id"] == release_id
        # Reuse existing approved capabilities through the actual persistent registry,
        # then let the real GENESIS resolver consume Backend's scoped catalog.
        it_actor = (
            await app.state.auth_service.whoami(
                context.headers["it"]["Authorization"].split(" ", 1)[1]
            )
        )["actor"]["actor_id"]
        capability_registry = app.state.factory_capability_registry
        for capability_id in ("global.search", "organization.context.read"):
            entry = await capability_registry.register(
                {
                    "tenant_id": "tenant_business",
                    "organization_id": "org_business",
                    "workspace_id": "workspace_business",
                    "capability_id": capability_id,
                    "version": "1.0.0",
                    "name": "Informasi operasional perusahaan",
                    "purpose": "Provide company operating context",
                    "owner": lead,
                    "capability_type": "REPORT",
                    "lifecycle_state": "DEFINED",
                    "risk_level": "LOW",
                    "availability": "AVAILABLE",
                    "configuration_status": "CONFIGURED",
                    "scope_refs": draft["scope_refs"],
                    "permission_refs": [],
                    "backing_tool_ids": [],
                },
                tenant_id="tenant_business",
                organization_id="org_business",
                workspace_id="workspace_business",
                actor_id=lead,
                correlation_id="corr_reuse_catalog",
            )
            await capability_registry.approve(
                tenant_id=entry.tenant_id,
                workspace_id=entry.workspace_id,
                subject_id=entry.subject_id,
                version=entry.version,
                actor_id=it_actor,
                decision_id=uuid4().hex,
                authority=DecisionAuthority.IT,
                correlation_id="corr_reuse_decision",
            )
            await capability_registry.activate(
                tenant_id=entry.tenant_id,
                workspace_id=entry.workspace_id,
                subject_id=entry.subject_id,
                version=entry.version,
                actor_id=it_actor,
                release_id="release_existing_" + uuid4().hex,
                correlation_id="corr_reuse_activation",
            )
        reuse_request = await context.client.post(
            path,
            headers=context.headers["member"],
            json={
                "need": "Company operating context",
                "goal": "Help humans understand their company",
            },
        )
        assert reuse_request.status_code == 201, reuse_request.text
        reuse_id = reuse_request.json()["request_id"]
        validation = await submit(context, "CAPABILITY_REQUEST", reuse_id)
        await action(context, validation, "lead", 0)
        reuse = await context.client.post(
            f"{path}/{reuse_id}/resolve",
            headers=context.headers["lead"],
            json={"reason": "Gunakan bantuan yang telah tersedia"},
        )
        assert reuse.status_code == 200, reuse.text
        reused = reuse.json()["factory_result"]
        assert reused["decision"] == "REUSE" and reused["registry_result"] is None
        assert {item["capability_id"] for item in reused["existing_capability_refs"]} == {
            "global.search",
            "organization.context.read",
        }
        app.dependency_overrides.pop(get_genesis_client)
