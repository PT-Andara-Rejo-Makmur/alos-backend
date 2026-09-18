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
TOOL_RESULT_SCHEMA_ID = "https://schemas.alos.dev/v1/tool/tool-result.schema.json"


class ToolContractValidator(Protocol):
    def validate_request(self, payload: Mapping[str, Any]) -> None: ...

    def validate_result(self, payload: Mapping[str, Any]) -> None: ...


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
        required_schemas = {TOOL_REQUEST_SCHEMA_ID, TOOL_RESULT_SCHEMA_ID}
        if not required_schemas.issubset(documents):
            raise ValueError("Canonical ToolRequest/ToolResult schemas were not found")
        registry = Registry().with_resources(
            (schema_id, Resource.from_contents(schema)) for schema_id, schema in documents.items()
        )
        self._request_validator = Draft202012Validator(
            documents[TOOL_REQUEST_SCHEMA_ID],
            registry=registry,
            format_checker=FormatChecker(),
        )
        self._result_validator = Draft202012Validator(
            documents[TOOL_RESULT_SCHEMA_ID],
            registry=registry,
            format_checker=FormatChecker(),
        )

    def validate_request(self, payload: Mapping[str, Any]) -> None:
        self._validate(
            self._request_validator,
            payload,
            code="TOOL_REQUEST_INVALID",
            message="ToolRequest does not satisfy the canonical contract.",
            status_code=422,
        )

    def validate_result(self, payload: Mapping[str, Any]) -> None:
        self._validate(
            self._result_validator,
            payload,
            code="TOOL_RESULT_INVALID",
            message="ToolResult does not satisfy the canonical contract.",
            status_code=500,
        )

    @staticmethod
    def _validate(
        validator: Draft202012Validator,
        payload: Mapping[str, Any],
        *,
        code: str,
        message: str,
        status_code: int,
    ) -> None:
        errors = sorted(validator.iter_errors(dict(payload)), key=lambda item: list(item.path))
        if errors:
            first = errors[0]
            path = ".".join(str(part) for part in first.path) or "$"
            raise PlatformError(
                code,
                message,
                status_code=status_code,
                details={"path": path, "reason": first.message},
            )
