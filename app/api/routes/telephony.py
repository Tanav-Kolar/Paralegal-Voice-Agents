import logging

from fastapi import APIRouter, HTTPException, Request, status

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/exotel/passthru", status_code=status.HTTP_200_OK)
async def exotel_passthru(request: Request) -> dict[str, str]:
    """Acknowledge an Exotel Passthru callback without storing caller data."""
    call_sid = request.query_params.get("CallSid") or request.query_params.get("callsid")
    if not call_sid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="callsid required",
        )

    logger.info(
        "exotel_passthru_received call_sid=%s direction=%s",
        call_sid,
        request.query_params.get("Direction")
        or request.query_params.get("direction", "unknown"),
    )
    return {"status": "received", "provider": "exotel", "call_sid": call_sid}
