from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class ToolAdapter(Protocol):
    async def execute(self, arguments: Mapping[str, Any]) -> Any: ...
