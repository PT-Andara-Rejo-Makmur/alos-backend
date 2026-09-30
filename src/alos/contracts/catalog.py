"""Read-only access to canonical JSON Schemas owned by alos-contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource


class ContractValidationError(ValueError):
    def __init__(self, schema_id: str, path: str, reason: str) -> None:
        super().__init__(f"{schema_id} at {path}: {reason}")
        self.schema_id = schema_id
        self.path = path
        self.reason = reason

    def __str__(self) -> str:
        return f"{self.schema_id} at {self.path}: {self.reason}"


class CanonicalContractCatalog:
    """Load schemas without importing implementation code from another repository."""

    def __init__(self, contracts_root: Path) -> None:
        documents: dict[str, dict[str, Any]] = {}
        for pattern in ("schemas/**/*.schema.json", "events/**/*.schema.json"):
            for path in contracts_root.glob(pattern):
                loaded = json.loads(path.read_text(encoding="utf-8"))
                document = cast(dict[str, Any], loaded)
                schema_id = document.get("$id")
                if isinstance(schema_id, str):
                    documents[schema_id] = document
        if not documents:
            raise ValueError("No canonical schemas were found in ALOS_CONTRACTS_PATH")
        self._documents = documents
        self._registry = Registry().with_resources(
            (schema_id, Resource.from_contents(schema)) for schema_id, schema in documents.items()
        )

    def has_schema(self, schema_id: str) -> bool:
        return schema_id in self._documents

    def enum_values(self, schema_id: str, definition: str) -> tuple[str, ...]:
        schema = self._documents.get(schema_id)
        if schema is None:
            raise ValueError(f"Canonical schema is not available: {schema_id}")
        values = (
            schema.get("enum")
            if not definition
            else schema.get("$defs", {}).get(definition, {}).get("enum")
        )
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            raise ValueError(f"Canonical enum is not available: {definition}")
        return tuple(values)

    def validate(self, schema_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        document_id, separator, _fragment = schema_id.partition("#")
        schema = self._documents.get(document_id)
        if schema is None:
            raise ValueError(f"Canonical schema is not available: {schema_id}")
        validator = Draft202012Validator(
            {"$ref": schema_id} if separator else schema,
            registry=self._registry,
            format_checker=FormatChecker(),
        )
        errors = sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path))
        if errors:
            first = errors[0]
            path = ".".join(str(part) for part in first.path) or "$"
            raise ContractValidationError(schema_id, path, first.message)
        return dict(payload)
