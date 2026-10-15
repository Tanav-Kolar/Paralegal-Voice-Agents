import logging
from datetime import UTC, datetime
from secrets import compare_digest
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.config import Settings, get_settings
from app.db.mongodb import get_call_events_collection

router = APIRouter()
logger = logging.getLogger(__name__)

# Exotel status fields we keep. Caller/callee numbers (From/To) are deliberately excluded.
STATUS_FIELDS = {
    "Status": "call_status",
    "Duration": "duration_seconds",
    "ConversationDuration": "conversation_duration_seconds",
    "StartTime": "provider_start_time",
    "EndTime": "provider_end_time",
    "RecordingUrl": "recording_url",
}


def _param(params: dict[str, str], *names: str) -> str | None:
    return next((params[n] for n in names if params.get(n)), None)


def _require_call_sid(params: dict[str, str]) -> str:
    call_sid = _param(params, "CallSid", "callsid")
    if not call_sid:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="callsid required",
        )
    return call_sid


def _require_webhook_token(request: Request, settings: Settings) -> None:
    expected_token = settings.exotel_webhook_token
    provided_token = request.query_params.get("token", "")
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Exotel webhook token is not configured",
        )
    if not compare_digest(provided_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized Exotel webhook",
        )


async def _record_event(collection, call_sid: str, event_type: str, **fields) -> None:
    await collection.insert_one(
        {
            "provider": "exotel",
            "provider_call_id": call_sid,
            "event_type": event_type,
            "received_at": datetime.now(UTC),
            **fields,
        }
    )
    logger.info("exotel_%s_received call_sid=%s", event_type, call_sid)


@router.get("/exotel/passthru", status_code=status.HTTP_200_OK)
async def exotel_passthru(
    request: Request,
    settings: Settings = Depends(get_settings),
    collection=Depends(get_call_events_collection),
) -> dict[str, str]:
    """Record an Exotel Passthru callback as a lifecycle event, without caller data."""
    _require_webhook_token(request, settings)
    params = dict(request.query_params)
    call_sid = _require_call_sid(params)
    direction = _param(params, "Direction", "direction") or "unknown"
    await _record_event(collection, call_sid, "passthru", direction=direction)
    return {"status": "received", "provider": "exotel", "call_sid": call_sid}


@router.api_route("/exotel/status", methods=["GET", "POST"], status_code=status.HTTP_200_OK)
async def exotel_status(
    request: Request,
    settings: Settings = Depends(get_settings),
    collection=Depends(get_call_events_collection),
) -> dict[str, str]:
    """Record an Exotel call-status callback (query string or form body)."""
    _require_webhook_token(request, settings)
    # ponytail: stdlib form parsing avoids python-multipart; JSON callbacks are not handled.
    body = (await request.body()).decode()
    params = {**dict(request.query_params), **dict(parse_qsl(body))}
    call_sid = _require_call_sid(params)
    fields = {name: params[key] for key, name in STATUS_FIELDS.items() if params.get(key)}
    await _record_event(collection, call_sid, "status", **fields)
    return {"status": "received", "provider": "exotel", "call_sid": call_sid}
