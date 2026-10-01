"""Narrow Shared Work document visibility port for business record owners."""

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from alos.identity import Principal


class DocumentReferencePort(Protocol):
    async def validate_document_reference(
        self, session: AsyncSession, principal: Principal, document_id: str
    ) -> None: ...
