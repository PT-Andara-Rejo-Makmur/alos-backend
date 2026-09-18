"""Validation of GENESIS integration payloads against alos-contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

INTEGRATION_SCHEMA_ID = "https://schemas.alos.dev/v1/common/integration-diagnostic.schema.json"


@dataclass(frozen=True, slots=True)
class IntegrationContractError(Exception):
    path: str
    reason: str


class IntegrationContractValidator:
    """Validate public and internal diagnostic payloads using canonical schemas."""

    def __init__(self, contracts_root: Path) -> None:
        documents: dict[str, dict[str, Any]] = {}
        for pattern in ("schemas/**/*.schema.json", "events/**/*.schema.json"):
            for path in contracts_root.glob(pattern):
                document = json.loads(path.read_text(encoding="utf-8"))
                schema_id = document.get("$id")
                if schema_id:
                    documents[str(schema_id)] = document
        if INTEGRATION_SCHEMA_ID not in documents:
            raise ValueError("Canonical IntegrationDiagnostic schema was not found")

        registry = Registry().with_resources(
            (schema_id, Resource.from_contents(schema)) for schema_id, schema in documents.items()
        )
        base_schema = documents[INTEGRATION_SCHEMA_ID]
        self._public_validator = Draft202012Validator(
            base_schema,
            registry=registry,
            format_checker=FormatChecker(),
        )
        genesis_schema = base_schema["$defs"]["genesis_response"]
        self._genesis_validator = Draft202012Validator(
            genesis_schema,
            registry=registry,
            format_checker=FormatChecker(),
        )

    @staticmethod
    def _validate(
        validator: Draft202012Validator,
        payload: Mapping[str, Any],
    ) -> None:
        errors = sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path))
        if errors:
            first = errors[0]
            path = ".".join(str(part) for part in first.path) or "$"
            raise IntegrationContractError(path=path, reason=first.message)

    def validate_genesis_response(self, payload: Mapping[str, Any]) -> None:
        self._validate(self._genesis_validator, payload)

    def validate_public_response(self, payload: Mapping[str, Any]) -> None:
        self._validate(self._public_validator, payload)
