"""Thin FastAPI adapters; canonical JSON Schemas own every request field and rule."""

from typing import Any, ClassVar

from pydantic import JsonValue, RootModel

from alos.contracts import CanonicalContractCatalog, ContractValidationError
from alos.security.errors import PlatformError

SCHEMA_BASE = "https://schemas.alos.dev/v1/strategy/"


class StrategyRequest(RootModel[dict[str, JsonValue]]):
    schema_file: ClassVar[str]

    def validated(self, contracts: CanonicalContractCatalog) -> dict[str, Any]:
        """Validate before converting JSON into internal Strategy domain records."""
        try:
            return contracts.validate(SCHEMA_BASE + self.schema_file, self.root)
        except ContractValidationError as exc:
            raise PlatformError(
                "STRATEGY_CONTRACT_INVALID",
                "Strategy request does not satisfy its canonical contract.",
                status_code=422,
                details={"path": exc.path},
            ) from exc
        except ValueError as exc:
            raise PlatformError(
                "CONTRACTS_UNAVAILABLE",
                "Canonical Strategy contract is unavailable.",
                status_code=503,
            ) from exc


class StrategyPlanCreateRequest(StrategyRequest):
    schema_file = "strategy-plan-create-request.schema.json"


class StrategyPlanUpdateRequest(StrategyRequest):
    schema_file = "strategy-plan-update-request.schema.json"


class StrategicObjectiveCreateRequest(StrategyRequest):
    schema_file = "strategic-objective-create-request.schema.json"


class BusinessTargetCreateRequest(StrategyRequest):
    schema_file = "business-target-create-request.schema.json"


class MetricObservationCreateRequest(StrategyRequest):
    schema_file = "metric-observation-create-request.schema.json"


class PlanningAssumptionCreateRequest(StrategyRequest):
    schema_file = "planning-assumption-create-request.schema.json"


class TargetRelationshipCreateRequest(StrategyRequest):
    schema_file = "target-relationship-create-request.schema.json"


class CascadePreviewRequest(StrategyRequest):
    schema_file = "cascade-preview-request.schema.json"


class CascadeAcceptRequest(StrategyRequest):
    schema_file = "cascade-accept-request.schema.json"


class TargetRevisionCreateRequest(StrategyRequest):
    schema_file = "target-revision-create-request.schema.json"


def request_contract(model: type[StrategyRequest]) -> dict[str, Any]:
    """Expose the same public schema as the canonical OpenAPI document."""
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": SCHEMA_BASE + model.schema_file},
                }
            },
        }
    }


class BusinessTargetUpdateRequest(StrategyRequest):
    schema_file = "business-target-update-request.schema.json"


class StrategyVerificationRequest(StrategyRequest):
    schema_file = "strategy-verification-request.schema.json"
