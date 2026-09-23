from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from alos.config import Settings
from alos.dependencies import get_genesis_client
from alos.main import create_app

WORKSPACE = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = WORKSPACE / "alos-contracts"


class ReviewGenesisStub:
    def __init__(self) -> None:
        self.calls = 0

    async def review(self, payload: Mapping[str, Any], *, correlation_id: str) -> dict[str, Any]:
        del payload, correlation_id
        self.calls += 1
        raise AssertionError("unauthorized review must not reach GENESIS")


@pytest.mark.asyncio
async def test_public_review_rejects_forged_observations_before_genesis() -> None:
    settings = Settings(
        _env_file=None,
        APP_ENV="test",
        DATABASE_URL="postgresql+asyncpg://alos:alos@localhost:5432/alos_test",
        GENESIS_BASE_URL="http://genesis.test",
        GENESIS_INTERNAL_TOKEN="test-only-token",  # noqa: S106
        ALOS_CONTRACTS_PATH=CONTRACTS_ROOT,
        ENABLE_TEST_TOOLS=True,
    )
    app = create_app(settings)
    genesis = ReviewGenesisStub()
    app.dependency_overrides[get_genesis_client] = lambda: genesis
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        registration = {
            "email": "review-authority@andara.local",
            "password": "StrongPass!123",
            "display_name": "Review Authority",
            "tenant_id": "tenant_review_api",
            "organization_id": "org_review_api",
            "workspace_id": "workspace_review_api",
            "roles": ["DIVISION_MEMBER"],
            "permissions": ["tools.diagnostic.execute"],
            "scopes": ["scope.diagnostic"],
            "data_scope": "PROJECT",
        }
        await client.post("/api/v1/auth/register", json=registration)
        login = await client.post(
            "/api/v1/auth/login",
            json={"email": registration["email"], "password": registration["password"]},
        )
        token = str(login.json()["access_token"])
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Correlation-ID": "corr.review.api.001",
        }
        bootstrap_response = await client.post("/api/v1/integration/bootstrap", headers=headers)
        assert bootstrap_response.status_code == 200
        bootstrap = bootstrap_response.json()

        invocation = json.loads(
            (CONTRACTS_ROOT / "examples/review/review-invocation.json").read_text(encoding="utf-8")
        )
        invocation.pop("$schema")
        subject = invocation["subject"]
        identity = {
            "tenant_id": registration["tenant_id"],
            "organization_id": registration["organization_id"],
            "workspace_id": registration["workspace_id"],
            "correlation_id": headers["X-Correlation-ID"],
        }
        subject.update(
            {
                **identity,
                "review_id": "review.authority.api.001",
                "subject_id": bootstrap["agent_id"],
                "subject_version": bootstrap["agent_version"],
                "purpose": "Validate the governed Backend and GENESIS runtime boundary.",
                "business_context": {
                    "registry_digest": bootstrap["registry_digest"],
                    "subject_type": "agent",
                },
                "evidence_refs": [bootstrap["evidence_ref"]],
            }
        )
        subject["capability"].update(
            {
                    "owner": login.json()["principal"]["actor"]["actor_id"],
                "purpose": subject["purpose"],
                "tool_ids": ["diagnostic.echo"],
                "permission_refs": ["tools.diagnostic.execute"],
                "risk_level": "LOW",
            }
        )
        subject["tools"] = [
            {
                "tool_id": "diagnostic.echo",
                "purpose": "Backend-authorized tool: diagnostic.echo",
                "backend_executor": True,
            }
        ]
        evaluation_subject = invocation["evaluation_subject"]
        evaluation_subject.update(
            {
                **identity,
                "subject_id": bootstrap["agent_id"],
                "subject_version": bootstrap["agent_version"],
                "observations": {
                    "assurance.runtime.budget_enforced": {
                        "status": "PASS",
                        "fixture_ids": ["fixture.caller.controlled"],
                    }
                },
            }
        )
        denied = await client.post("/api/v1/reviews", json=invocation, headers=headers)
        malformed = await client.post(
            "/api/v1/reviews", json={"subject": copy.deepcopy(subject)}, headers=headers
        )
        evaluation_subject["observations"] = {}
        await app.state.agent_registry.suspend(
            tenant_id=registration["tenant_id"],
            workspace_id=registration["workspace_id"],
            subject_id=bootstrap["agent_id"],
            version=bootstrap["agent_version"],
                actor_id=login.json()["principal"]["actor"]["actor_id"],
            correlation_id=headers["X-Correlation-ID"],
        )
        suspended = await client.post("/api/v1/reviews", json=invocation, headers=headers)

    assert denied.status_code == 403
    assert denied.json()["code"] == "REVIEW_FACTS_NOT_AUTHORITATIVE"
    assert malformed.status_code == 422
    assert malformed.json()["code"] == "REVIEW_REQUEST_INVALID"
    assert suspended.status_code == 403
    assert suspended.json()["code"] == "REVIEW_NOT_AUTHORIZED"
    assert genesis.calls == 0
