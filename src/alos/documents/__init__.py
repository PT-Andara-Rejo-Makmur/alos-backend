"""Document metadata and access boundary; raw storage is adapter-owned."""

from alos.documents.models import DocumentMetadata, DocumentVersion
from alos.documents.service import DocumentRegistry, DocumentRegistryError

__all__ = [
    "DocumentMetadata",
    "DocumentRegistry",
    "DocumentRegistryError",
    "DocumentVersion",
]
