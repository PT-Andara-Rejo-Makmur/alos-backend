from fastapi import APIRouter, Depends

from alos.authentication.internal import verify_internal_token

router = APIRouter(
    prefix="/internal/v1",
    tags=["internal"],
    dependencies=[Depends(verify_internal_token)],
)


@router.get("/health", include_in_schema=False)
async def internal_health() -> dict[str, str]:
    """Real liveness signal for trusted service-to-service connectivity."""
    return {"status": "ok", "boundary": "alos-backend"}
