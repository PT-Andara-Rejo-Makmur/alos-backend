from fastapi import APIRouter, Request

from alos import __version__
from alos.api.models import SystemInfoResponse

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/system/info", response_model=SystemInfoResponse)
async def system_info(request: Request) -> SystemInfoResponse:
    settings = request.app.state.settings
    return SystemInfoResponse(
        service="alos-backend",
        version=__version__,
        environment=settings.APP_ENV,
    )
