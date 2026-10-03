"""Immutable content-addressed objects on the configured shared document volume."""

import hashlib
import re
from pathlib import Path


class DocumentObjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def path(self, boundary: tuple[str, str, str], digest: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("invalid object hash")
        partition = hashlib.sha256(":".join(boundary).encode()).hexdigest()
        result = self.root / partition / digest[:2] / digest
        if not result.resolve().is_relative_to(self.root):
            raise ValueError("object boundary is invalid")
        return result

    def put(self, boundary: tuple[str, str, str], content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        destination = self.path(boundary, digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replacement prevents workers from reading an incomplete object.
        from uuid import uuid4

        pending = destination.with_name(uuid4().hex)
        try:
            with pending.open("xb") as stream:
                stream.write(content)
            pending.replace(destination)
        finally:
            pending.unlink(missing_ok=True)
        return digest

    def read(self, boundary: tuple[str, str, str], digest: str, limit: int) -> bytes:
        with self.path(boundary, digest).open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("object integrity failed")
        return data
