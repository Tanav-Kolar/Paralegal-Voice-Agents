"""Browser-driven demo sessions for the hosted prototype.

Sessions run the same Session as a real call, over a BrowserTransport (raw PCM frames), so the
demo exercises the same consent, language, recording and transcript pipeline.
"""

import asyncio
from secrets import token_hex
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, WebSocket, status

from app.api.routes.telephony import (
    _create_voicebot_intake_call,
    _voicebot_session_token_hash,
    run_voicebot_stream,
)
from app.core.config import Settings, get_settings
from app.db.mongodb import (
    get_call_events_collection,
    get_intake_audit_events_collection,
    get_intake_calls_collection,
)
from app.security.access import ACCESS_COOKIE, demo_access_ready, has_access, require_access
from app.security.field_encryption import FieldEncryptor, get_field_encryptor
from app.voice.transport import BrowserTransport

router = APIRouter()

# ponytail: per-instance tracking; use a shared store if Cloud Run scales past one instance.
_active_sessions: set[str] = set()


@router.post("/session", dependencies=[Depends(require_access)])
async def create_demo_session(
    settings: Settings = Depends(get_settings),
    encryptor: FieldEncryptor = Depends(get_field_encryptor),
    intake_collection=Depends(get_intake_calls_collection),
    audit_collection=Depends(get_intake_audit_events_collection),
) -> dict[str, str]:
    """Create a demo intake call and return the stream path for the browser to open."""
    session_token = token_hex(32)
    await _create_voicebot_intake_call(
        collection=intake_collection,
        audit_collection=audit_collection,
        encryptor=encryptor,
        call_sid=f"demo-{uuid4()}",
        caller_phone="Browser demo",
        session_token=session_token,
        provider="demo",
    )
    created = await intake_collection.find_one(
        {"voicebot_session_token_hash": _voicebot_session_token_hash(session_token)}, {"id": 1}
    )
    if not created:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Could not start a demo session")
    return {
        "intakeCallId": str(created["id"]),
        "streamPath": f"{settings.api_v1_prefix}/demo/stream?session_token={session_token}",
    }


@router.websocket("/stream")
async def demo_stream(
    websocket: WebSocket,
    settings: Settings = Depends(get_settings),
    collection=Depends(get_call_events_collection),
    intake_collection=Depends(get_intake_calls_collection),
    audit_collection=Depends(get_intake_audit_events_collection),
    encryptor: FieldEncryptor = Depends(get_field_encryptor),
) -> None:
    if not demo_access_ready(settings) or not has_access(websocket.cookies, settings):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    session_token = websocket.query_params.get("session_token", "")
    demo_call = await intake_collection.find_one(
        {
            "voicebot_session_token_hash": _voicebot_session_token_hash(session_token),
            "telephony.provider": "demo",
        },
        {"id": 1},
    )
    if not demo_call:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    viewer = websocket.cookies.get(ACCESS_COOKIE, "")
    if viewer in _active_sessions or len(_active_sessions) >= settings.demo_max_concurrent_sessions:
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER)
        return
    _active_sessions.add(viewer)
    try:
        async with asyncio.timeout(settings.demo_session_max_seconds):
            await run_voicebot_stream(
                BrowserTransport(websocket),
                session_token=session_token,
                settings=settings,
                collection=collection,
                intake_collection=intake_collection,
                audit_collection=audit_collection,
                encryptor=encryptor,
            )
    except TimeoutError:
        await websocket.close(code=status.WS_1000_NORMAL_CLOSURE)
    finally:
        _active_sessions.discard(viewer)
