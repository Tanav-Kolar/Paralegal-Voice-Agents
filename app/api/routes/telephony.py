import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.db.mongodb import get_call_events_collection

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/exotel/passthru", status_code=status.HTTP_200_OK)
async def exotel_passthru(
    request: Request,
    collection=Depends(get_call_events_collection),
) -> dict[str, str]:
    """Record an Exotel Passthru callback as a lifecycle event, without caller data."""
    call_sid = request.query_params.get("CallSid") or request.query_params.get("callsid")
    if not call_sid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="callsid required",
        )

    direction = (
        request.query_params.get("Direction")
        or request.query_params.get("direction")
        or "unknown"
    )
    await collection.insert_one(
        {
            "provider": "exotel",
            "provider_call_id": call_sid,
            "event_type": "passthru",
            "direction": direction,
            "received_at": datetime.now(UTC),
        }
    )
    logger.info("exotel_passthru_received call_sid=%s direction=%s", call_sid, direction)
    return {"status": "received", "provider": "exotel", "call_sid": call_sid}
