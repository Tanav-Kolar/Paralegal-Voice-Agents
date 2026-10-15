import logging
from asyncio import Lock
from base64 import b64decode, b64encode
from binascii import Error as BinasciiError
from datetime import UTC, datetime
from hashlib import sha256
from json import dumps
from secrets import compare_digest
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)

from app.core.config import Settings, get_settings
from app.db.mongodb import get_call_events_collection
from app.providers.contracts import (
    StreamingSpeechToTextSession,
    StreamingTextToSpeechProvider,
    TranscriptHandler,
)
from app.providers.sarvam import SarvamRealtimeSpeechToTextSession, SarvamStreamingTextToSpeech
from app.workflows.consent import (
    ENGLISH_CONSENT_DECLINED_RESPONSE,
    ENGLISH_CONSENT_GRANTED_RESPONSE,
    ENGLISH_CONSENT_PROMPT,
    ENGLISH_CONSENT_REPROMPT,
    ENGLISH_CONSENT_UNCLEAR_CLOSURE,
    ConsentAction,
    ConsentState,
    ConsentWorkflow,
)

router = APIRouter()
logger = logging.getLogger(__name__)

# Exotel status fields we keep. Caller/callee numbers (From/To) are deliberately excluded.
STATUS_FIELDS = {
    "Status": "call_status",
    "Duration": "duration_seconds",
    "ConversationDuration": "conversation_duration_seconds",
    "StartTime": "provider_start_time",
    "EndTime": "provider_end_time",
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


def _recording_available(params: dict[str, str]) -> bool | None:
    recording_url = params.get("RecordingUrl")
    if recording_url is None:
        return None
    return recording_url.lower() not in {"", "null", "none"}


def _voicebot_stream_url(settings: Settings) -> str:
    if not settings.public_base_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Public base URL is not configured",
        )
    public_url = urlsplit(settings.public_base_url)
    if public_url.scheme not in {"http", "https"} or not public_url.netloc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Public base URL must be an HTTP(S) URL",
        )
    if not settings.exotel_voicebot_username or not settings.exotel_voicebot_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Exotel Voicebot credentials are not configured",
        )
    userinfo = ":".join(
        (
            quote(settings.exotel_voicebot_username, safe=""),
            quote(settings.exotel_voicebot_password, safe=""),
        )
    )
    return urlunsplit(
        (
            "wss" if public_url.scheme == "https" else "ws",
            f"{userinfo}@{public_url.netloc}",
            "/api/v1/telephony/exotel/voicebot/stream",
            "",
            "",
        )
    )


def _is_valid_voicebot_auth(websocket: WebSocket, settings: Settings) -> bool:
    authorization = websocket.headers.get("authorization", "")
    if not authorization.startswith("Basic "):
        return False
    try:
        encoded_credentials = authorization.removeprefix("Basic ")
        username, password = b64decode(encoded_credentials, validate=True).decode().split(":", 1)
    except (BinasciiError, UnicodeDecodeError, ValueError):
        return False
    return bool(
        settings.exotel_voicebot_username
        and settings.exotel_voicebot_password
        and compare_digest(username, settings.exotel_voicebot_username)
        and compare_digest(password, settings.exotel_voicebot_password)
    )


def _create_stt_session(
    settings: Settings, on_transcript: TranscriptHandler
) -> StreamingSpeechToTextSession:
    if settings.speech_to_text_provider != "sarvam":
        raise ValueError("Sarvam must be configured as the speech-to-text provider")
    return SarvamRealtimeSpeechToTextSession(settings, on_transcript)


def _create_tts_provider(settings: Settings) -> StreamingTextToSpeechProvider:
    if settings.text_to_speech_provider != "sarvam":
        raise ValueError("Sarvam must be configured as the text-to-speech provider")
    return SarvamStreamingTextToSpeech(settings)


def _event_key(call_sid: str, event_type: str, fields: dict[str, object]) -> str:
    payload = {
        "provider": "exotel",
        "provider_call_id": call_sid,
        "event_type": event_type,
        "fields": fields,
    }
    return sha256(dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def _record_event(collection, call_sid: str, event_type: str, **fields) -> bool:
    event_key = _event_key(call_sid, event_type, fields)
    result = await collection.update_one(
        {"event_key": event_key},
        {
            "$setOnInsert": {
                "event_key": event_key,
                "provider": "exotel",
                "provider_call_id": call_sid,
                "event_type": event_type,
                "received_at": datetime.now(UTC),
                **fields,
            }
        },
        upsert=True,
    )
    inserted = result.upserted_id is not None
    logger.info(
        "exotel_%s_%s call_sid=%s",
        event_type,
        "received" if inserted else "duplicate",
        call_sid,
    )
    return inserted


async def _record_voicebot_session_event(
    collection, stream_sid: str | None, event_type: str
) -> None:
    """Record Voicebot lifecycle metadata without retaining media or transcript content."""
    if not stream_sid:
        logger.warning("exotel_voicebot_%s_missing_stream_sid", event_type)
        return
    try:
        await _record_event(collection, stream_sid, event_type, stream_sid=stream_sid)
    except Exception:
        logger.exception(
            "exotel_voicebot_%s_event_record_failed stream_sid=%s", event_type, stream_sid
        )


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
    inserted = await _record_event(collection, call_sid, "passthru", direction=direction)
    return {
        "status": "received" if inserted else "duplicate",
        "provider": "exotel",
        "call_sid": call_sid,
    }


@router.get("/exotel/voicebot", status_code=status.HTTP_200_OK)
async def exotel_voicebot_resolver(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Return the authenticated WebSocket URL for an Exotel Voicebot call."""
    _require_webhook_token(request, settings)
    _require_call_sid(dict(request.query_params))
    return {"url": _voicebot_stream_url(settings)}


@router.websocket("/exotel/voicebot/stream")
async def exotel_voicebot_stream(
    websocket: WebSocket,
    settings: Settings = Depends(get_settings),
    collection=Depends(get_call_events_collection),
) -> None:
    """Accept Exotel Voicebot messages without retaining or logging media payloads."""
    if not _is_valid_voicebot_auth(websocket, settings):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    stream_sid: str | None = None
    stt_session: StreamingSpeechToTextSession | None = None
    consent_workflow = ConsentWorkflow()
    speech_lock = Lock()

    async def speak(text: str) -> None:
        async with speech_lock:
            tts_provider = _create_tts_provider(settings)
            async for audio in tts_provider.stream(text):
                await websocket.send_json(
                    {
                        "event": "media",
                        "stream_sid": stream_sid,
                        "media": {"payload": b64encode(audio.content).decode()},
                    }
                )

    async def on_transcript(event_type: str, transcript: str, language: str | None) -> None:
        logger.info(
            "sarvam_stt_transcript_received stream_sid=%s event=%s language=%s characters=%d",
            stream_sid,
            event_type,
            language or "unknown",
            len(transcript),
        )
        if event_type != "transcript.final":
            return
        if consent_workflow.state != ConsentState.AWAITING_CONSENT:
            logger.info(
                "consent_transcript_ignored stream_sid=%s state=%s",
                stream_sid,
                consent_workflow.state,
            )
            return

        action = consent_workflow.handle_response(transcript)
        response = {
            ConsentAction.REPROMPT_CONSENT: ENGLISH_CONSENT_REPROMPT,
            ConsentAction.CONSENT_GRANTED: ENGLISH_CONSENT_GRANTED_RESPONSE,
            ConsentAction.CONSENT_DECLINED: ENGLISH_CONSENT_DECLINED_RESPONSE,
            ConsentAction.CLOSE_CALL: ENGLISH_CONSENT_UNCLEAR_CLOSURE,
        }[action]
        await _record_voicebot_session_event(collection, stream_sid, f"voicebot_{action}")
        try:
            await speak(response)
        except (OSError, ValueError):
            logger.exception(
                "sarvam_tts_response_failed stream_sid=%s action=%s", stream_sid, action
            )
            await _record_voicebot_session_event(
                collection, stream_sid, "voicebot_tts_response_failed"
            )
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
            return

    async def speak_consent_prompt() -> None:
        if consent_workflow.begin() != ConsentAction.REQUEST_CONSENT:
            return
        await speak(ENGLISH_CONSENT_PROMPT)

    try:
        while True:
            message = await websocket.receive_json()
            event_type = message.get("event")
            if event_type == "start":
                stream_sid = message.get("start", {}).get("stream_sid")
                logger.info("exotel_voicebot_stream_started stream_sid=%s", stream_sid)
                await _record_voicebot_session_event(
                    collection, stream_sid, "voicebot_stream_started"
                )
                try:
                    stt_session = _create_stt_session(settings, on_transcript)
                    await stt_session.start()
                    await speak_consent_prompt()
                except (OSError, ValueError):
                    logger.exception("voicebot_session_start_failed stream_sid=%s", stream_sid)
                    await _record_voicebot_session_event(
                        collection, stream_sid, "voicebot_stt_start_failed"
                    )
                    await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                    return
            elif (
                event_type == "media"
                and stt_session
                and consent_workflow.state == ConsentState.AWAITING_CONSENT
            ):
                payload = message.get("media", {}).get("payload")
                if isinstance(payload, str) and payload:
                    await stt_session.send_audio(payload)
            elif event_type == "stop":
                logger.info("exotel_voicebot_stream_stopped stream_sid=%s", stream_sid)
                await _record_voicebot_session_event(
                    collection, stream_sid, "voicebot_stream_stopped"
                )
                if stt_session:
                    await stt_session.stop()
                await websocket.close()
                return
    except WebSocketDisconnect:
        logger.info("exotel_voicebot_stream_disconnected stream_sid=%s", stream_sid)
        await _record_voicebot_session_event(collection, stream_sid, "voicebot_stream_disconnected")
    finally:
        if stt_session:
            await stt_session.stop()


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
    recording_available = _recording_available(params)
    if recording_available is not None:
        fields["recording_available"] = recording_available
    inserted = await _record_event(collection, call_sid, "status", **fields)
    return {
        "status": "received" if inserted else "duplicate",
        "provider": "exotel",
        "call_sid": call_sid,
    }
