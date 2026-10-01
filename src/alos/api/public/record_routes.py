"""Small HTTP/schema adapters for fixed, explicitly registered owner resources."""

from typing import Any, ClassVar, Protocol, cast

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import JsonValue, RootModel
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from alos.contracts import CanonicalContractCatalog, ContractValidationError
from alos.dependencies import ContractCatalogDependency, CurrentPrincipalDependency
from alos.domains.record_repository import RecordSpec
from alos.identity import Principal
from alos.security.errors import PlatformError


class CanonicalRecordRequest(RootModel[dict[str, JsonValue]]):
    schema_uri: ClassVar[str]

    def validated(self, contracts: CanonicalContractCatalog) -> dict[str, Any]:
        try:
            return contracts.validate(self.schema_uri, self.root)
        except ContractValidationError as exc:
            raise PlatformError(
                "BUSINESS_CONTRACT_INVALID",
                "Request does not satisfy the canonical contract.",
                status_code=422,
                details={"path": exc.path},
            ) from exc
        except ValueError as exc:
            raise PlatformError(
                "CONTRACTS_UNAVAILABLE", "Canonical contract is unavailable.", status_code=503
            ) from exc


class RecordReadPort(Protocol):
    async def listing(
        self, resource: str, principal: Principal, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]: ...
    async def detail(
        self, resource: str, principal: Principal, identity: str
    ) -> dict[str, Any]: ...
    async def overview(
        self, principal: Principal, *, executive: bool = False
    ) -> dict[str, Any]: ...
    async def mutate(
        self,
        resource: str,
        principal: Principal,
        payload: dict[str, Any],
        identity: str | None = None,
        operation: str = "create",
    ) -> dict[str, Any]: ...


def response(
    contracts: CanonicalContractCatalog, uri: str, data: dict[str, Any], *, status: int = 200
) -> JSONResponse:
    try:
        contracts.validate(uri, data)
    except ValueError as exc:
        raise PlatformError(
            "BUSINESS_PROJECTION_UNAVAILABLE",
            "Canonical projection is unavailable.",
            status_code=503,
        ) from exc
    return JSONResponse(data, status_code=status)


def register_record_routes(
    domain: str,
    specs: dict[str, RecordSpec],
    models: dict[tuple[str, str], type[CanonicalRecordRequest]],
) -> APIRouter:
    """No dynamic table/schema HTTP parameter: each owner declares its resource set."""
    router = APIRouter(prefix=f"/api/v1/{domain}", tags=[domain])
    base = f"https://schemas.alos.dev/v1/{domain}/{domain}-contracts.schema.json"

    def service(request: Request) -> RecordReadPort:
        return cast(RecordReadPort, getattr(request.app.state, f"{domain}_service"))

    @router.get(
        "/overview", responses={200: {"content": {"application/json": {"schema": {"$ref": base}}}}}
    )
    async def overview(
        request: Request,
        principal: CurrentPrincipalDependency,
        contracts: ContractCatalogDependency,
    ) -> JSONResponse:
        try:
            data = await service(request).overview(principal)
        except SQLAlchemyError as exc:
            raise PlatformError(
                "BUSINESS_SOURCE_ERROR", "Domain source could not be read.", status_code=503
            ) from exc
        return response(contracts, base, data)

    def register(resource: str, spec: RecordSpec) -> None:
        path = "/" + resource.replace("_", "-")
        projection = base + "#/$defs/" + spec.name + "Projection"
        list_projection = base + "#/$defs/" + spec.name + "ListProjection"

        async def listing(
            request: Request,
            principal: CurrentPrincipalDependency,
            contracts: ContractCatalogDependency,
            limit: int = Query(100, ge=1, le=200),
            offset: int = Query(0, ge=0),
        ) -> JSONResponse:
            try:
                data = await service(request).listing(resource, principal, limit, offset)
            except SQLAlchemyError as exc:
                raise PlatformError(
                    "BUSINESS_SOURCE_ERROR", "Domain source could not be read.", status_code=503
                ) from exc
            return response(contracts, list_projection, data)

        async def detail(
            identity: str,
            request: Request,
            principal: CurrentPrincipalDependency,
            contracts: ContractCatalogDependency,
        ) -> JSONResponse:
            try:
                data = await service(request).detail(resource, principal, identity)
            except SQLAlchemyError as exc:
                raise PlatformError(
                    "BUSINESS_SOURCE_ERROR", "Domain source could not be read.", status_code=503
                ) from exc
            return response(contracts, projection, data)

        router.add_api_route(
            path,
            listing,
            methods=["GET"],
            name=f"{domain}_{resource}_list",
            responses={
                200: {"content": {"application/json": {"schema": {"$ref": list_projection}}}}
            },
        )
        router.add_api_route(
            path + "/{identity}",
            detail,
            methods=["GET"],
            name=f"{domain}_{resource}_detail",
            responses={200: {"content": {"application/json": {"schema": {"$ref": projection}}}}},
        )

        def mutation(mode: str) -> None:
            model = models[(resource, mode)]

            async def create(
                payload: CanonicalRecordRequest,
                request: Request,
                principal: CurrentPrincipalDependency,
                contracts: ContractCatalogDependency,
            ) -> JSONResponse:
                return await execute(payload, request, principal, contracts, None)

            async def change(
                identity: str,
                payload: CanonicalRecordRequest,
                request: Request,
                principal: CurrentPrincipalDependency,
                contracts: ContractCatalogDependency,
            ) -> JSONResponse:
                return await execute(payload, request, principal, contracts, identity)

            async def execute(
                payload: CanonicalRecordRequest,
                request: Request,
                principal: Principal,
                contracts: CanonicalContractCatalog,
                identity: str | None,
            ) -> JSONResponse:
                values = payload.validated(contracts)
                try:
                    data = await service(request).mutate(
                        resource, principal, values, identity, mode
                    )
                except IntegrityError as exc:
                    raise PlatformError(
                        "BUSINESS_REFERENCE_CONFLICT",
                        "Reference or unique key conflicts with existing records.",
                        status_code=409,
                    ) from exc
                except SQLAlchemyError as exc:
                    raise PlatformError(
                        "BUSINESS_SOURCE_ERROR", "Mutation could not be persisted.", status_code=503
                    ) from exc
                return response(
                    contracts, projection, data, status=201 if mode == "create" else 200
                )

            endpoint = create if mode == "create" else change
            endpoint.__annotations__["payload"] = model
            suffix = (
                ""
                if mode == "create"
                else "/{identity}" + ("/transition" if mode == "transition" else "")
            )
            router.add_api_route(
                path + suffix,
                endpoint,
                methods=["PATCH" if mode == "update" else "POST"],
                name=f"{domain}_{resource}_{mode}",
                status_code=201 if mode == "create" else 200,
                responses={
                    (201 if mode == "create" else 200): {
                        "content": {"application/json": {"schema": {"$ref": projection}}}
                    }
                },
                openapi_extra={
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"$ref": model.schema_uri}}},
                    }
                },
            )

        mutation("create")
        if not spec.immutable and spec.update_fields:
            mutation("update")
        if spec.transitions:
            mutation("transition")

    for resource, spec in specs.items():
        register(resource, spec)
    return router
