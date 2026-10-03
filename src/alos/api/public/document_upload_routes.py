"""Raw bounded file upload; browser input cannot choose storage paths or classification."""

from typing import Any

from fastapi import APIRouter, Query, Request

from alos.api.public.record_routes import response
from alos.dependencies import ContractCatalogDependency, CurrentPrincipalDependency
from alos.security.errors import PlatformError

router = APIRouter(prefix="/api/v1/documents", tags=["document-files"])
SCHEMA = "https://schemas.alos.dev/v1/document/document-upload.schema.json"


@router.post("/{document_id}/uploads", status_code=202)
async def upload_document(
    document_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
    filename: str = Query(min_length=1, max_length=180),
    version: str = Query(min_length=1, max_length=100, pattern=r"^[\w.-]+$"),
) -> Any:
    service = request.app.state.documents_service
    service.permission(principal, write=True)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > service.limit:
            raise PlatformError(
                "DOCUMENT_SIZE_INVALID", "Berkas melebihi batas ukuran.", status_code=413
            )
        data.extend(chunk)
    result = await service.upload(
        principal,
        document_id,
        filename,
        request.headers.get("content-type", "").split(";", 1)[0].strip(),
        version,
        bytes(data),
    )
    return response(contracts, SCHEMA, result, status=202)


@router.get("/uploads/{upload_id}")
async def document_upload_status(
    upload_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await request.app.state.documents_service.status(principal, upload_id)
    return response(contracts, SCHEMA, result)


@router.get("/{document_id}/content")
async def document_content(
    document_id: str,
    request: Request,
    principal: CurrentPrincipalDependency,
    contracts: ContractCatalogDependency,
) -> Any:
    result = await request.app.state.documents_service.get_document_content(principal, document_id)
    return response(
        contracts, "https://schemas.alos.dev/v1/document/document-content.schema.json", result
    )
