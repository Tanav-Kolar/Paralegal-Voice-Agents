from fastapi import APIRouter, Request, status
from pymongo.errors import PyMongoError

router = APIRouter()


@router.get("/health/live", status_code=status.HTTP_200_OK)
async def liveness() -> dict[str, str]:
    """Confirm that the API process is running."""
    return {"status": "ok"}


@router.get("/health/ready")
async def readiness(request: Request) -> dict[str, str]:
    """Confirm that the API can reach MongoDB."""
    try:
        await request.app.state.mongodb.client.admin.command("ping")
    except PyMongoError:
        return {"status": "unavailable", "database": "unavailable"}

    return {"status": "ok", "database": "ok"}
