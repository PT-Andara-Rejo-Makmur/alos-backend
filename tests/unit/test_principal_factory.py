import json
from pathlib import Path

import pytest

from alos.authentication.principal import PrincipalFactory
from alos.contracts import CanonicalContractCatalog, ContractValidationError

WORKSPACE = Path(__file__).resolve().parents[3]
CONTRACTS_ROOT = WORKSPACE / "alos-contracts"
FIXTURE = CONTRACTS_ROOT / "compatibility" / "fixtures" / "mvp1" / "execution-context.adapted.json"


def test_principal_factory_uses_canonical_execution_context() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.pop("$schema")
    factory = PrincipalFactory(CanonicalContractCatalog(CONTRACTS_ROOT))

    principal = factory.from_execution_context(payload)

    assert principal.actor_id == "actor_mvp1_it_lead"
    assert principal.roles == frozenset({"IT_LEAD", "PLATFORM_OPERATOR"})
    assert "permission.agent.run" in principal.permissions


def test_principal_factory_rejects_context_without_tenant() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    payload.pop("$schema")
    payload.pop("tenant_id")
    factory = PrincipalFactory(CanonicalContractCatalog(CONTRACTS_ROOT))

    with pytest.raises(ContractValidationError):
        factory.from_execution_context(payload)
