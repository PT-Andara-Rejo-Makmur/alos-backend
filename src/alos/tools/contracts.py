"""Runtime validation against canonical schemas from alos-contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from alos.security.errors import PlatformError

TOOL_REQUEST_SCHEMA_ID = "https://schemas.alos.dev/v1/tool/tool-request.schema.json"


class ToolContractValidator(Protocol):
    def validate_request(self, payload: Mapping[str, Any]) -> None: ...


class JsonSchemaToolContractValidator:
    """Loads canonical schemas from an alos-contracts checkout or packaged artifact."""

    def __init__(self, contracts_root: Path) -> None:
        documents: dict[str, dict[str, Any]] = {}
        paths = [
            *contracts_root.glob("schemas/**/*.schema.json"),
            *contracts_root.glob("events/**/*.schema.json"),
        ]
        for path in paths:
            document = json.loads(path.read_text(encoding="utf-8"))
            schema_id = document.get("$id")
            if schema_id:
                documents[str(schema_id)] = document
        if TOOL_REQUEST_SCHEMA_ID not in documents:
            raise ValueError("Canonical ToolRequest schema was not found in alos-contracts")
        registry = Registry().with_resources(
            (schema_id, Resource.from_contents(schema))
            for schema_id, schema in documents.items()
        )
        self._validator = Draft202012Validator(
            documents[TOOL_REQUEST_SCHEMA_ID],
            registry=registry,
            format_checker=FormatChecker(),
        )

    def validate_request(self, payload: Mapping[str, Any]) -> None:
        errors = sorted(
            self._validator.iter_errors(dict(payload)), key=lambda item: list(item.path)
        )
        if errors:
            first = errors[0]
            path = ".".join(str(part) for part in first.path) or "$"
            raise PlatformError(
                "TOOL_REQUEST_INVALID",
                "ToolRequest does not satisfy the canonical contract.",
                status_code=422,
                details={"path": path, "reason": first.message},
            )
