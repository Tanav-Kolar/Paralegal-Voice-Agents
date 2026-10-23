import logging
from base64 import b64decode
from binascii import Error as BinasciiError
from datetime import UTC, datetime
from hashlib import sha256
from hmac import new as hmac_new
from secrets import compare_digest
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    status,
)

from app.core.config import Settings, get_settings
from app.db.mongodb import (
    get_call_events_collection,
    get_intake_audit_events_collection,
    get_intake_calls_collection,
)
from app.providers.contracts import (
    ObjectStorageProvider,
    StreamingSpeechToTextSession,
    StreamingTextToSpeechProvider,
    TranscriptHandler,
)
from app.providers.gcs import GoogleCloudStorageProvider
from app.providers.sarvam import SarvamRealtimeSpeechToTextSession, SarvamStreamingTextToSpeech
from app.providers.sarvam_chat import SarvamConversationProvider
from app.schemas.intake_call import (
    CallStatus,
    IntakeCallCreateRequest,
    TelephonyDetailsInput,
)
from app.security.field_encryption import FieldEncryptor, get_field_encryptor
from app.services.audit import record_intake_creation_audit_events
from app.services.call_events import record_event
from app.services.intake_encryption import encrypt_intake_call
from app.voice.session import (  # noqa: F401  (prompts are re-exported for callers and tests)
    HINDI_LANGUAGE_SELECTION_PROMPT,
    LANGUAGE_SELECTION_PROMPT,
    Session,
)
from app.voice.transport import ExotelTransport, Transport
from app.workflows.conversation import (
    ConversationProvider,
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


def _require_caller_phone(params: dict[str, str]) -> str:
    caller_phone = _param(params, "CallFrom", "From")
    if not caller_phone:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="caller phone required",
        )
    return caller_phone


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


def _voicebot_session_token(settings: Settings, call_sid: str) -> str:
    return hmac_new(settings.exotel_webhook_token.encode(), call_sid.encode(), sha256).hexdigest()


def _voicebot_session_token_hash(session_token: str) -> str:
    return sha256(session_token.encode()).hexdigest()


def _voicebot_stream_url(settings: Settings, session_token: str) -> str:
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
            quote(f"{settings.exotel_voicebot_username}.{session_token}", safe=""),
            quote(settings.exotel_voicebot_password, safe=""),
        )
    )
    return urlunsplit(
        (
            "wss" if public_url.scheme == "https" else "ws",
            f"{userinfo}@{public_url.netloc}",
            "/api/v1/telephony/exotel/voicebot/stream",
            urlencode({"session_token": session_token}),
            "",
        )
    )


def _voicebot_session_token_from_auth(websocket: WebSocket, settings: Settings) -> str | None:
    authorization = websocket.headers.get("authorization", "")
    if not authorization.startswith("Basic "):
        return None
    try:
        encoded_credentials = authorization.removeprefix("Basic ")
        username, password = b64decode(encoded_credentials, validate=True).decode().split(":", 1)
    except (BinasciiError, UnicodeDecodeError, ValueError):
        return None
    if not settings.exotel_voicebot_username or not settings.exotel_voicebot_password:
        return None
    if not compare_digest(password, settings.exotel_voicebot_password):
        return None
    if compare_digest(username, settings.exotel_voicebot_username):
        return ""
    username_prefix = f"{settings.exotel_voicebot_username}."
    if not username.startswith(username_prefix):
        return None
    session_token = username.removeprefix(username_prefix)
    if len(session_token) != 64 or any(
        character not in "0123456789abcdef" for character in session_token
    ):
        return None
    return session_token


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


def _create_conversation_provider(settings: Settings) -> ConversationProvider:
    return SarvamConversationProvider(settings)


def _create_object_storage_provider(settings: Settings) -> ObjectStorageProvider:
    return GoogleCloudStorageProvider.from_settings(settings)


async def _create_voicebot_intake_call(
    *,
    collection,
    audit_collection,
    encryptor: FieldEncryptor,
    call_sid: str,
    caller_phone: str,
    session_token: str,
    provider: str = "exotel",
) -> None:
    intake_call = encrypt_intake_call(
        IntakeCallCreateRequest(
            status=CallStatus.CONSENT_PENDING,
            telephony=TelephonyDetailsInput(
                provider=provider,
                provider_call_id=call_sid,
                caller_phone=caller_phone,
                started_at=datetime.now(UTC),
            ),
        ),
        encryptor,
    )
    document = intake_call.model_dump(mode="json")
    document["voicebot_session_token_hash"] = _voicebot_session_token_hash(session_token)
    result = await collection.update_one(
        {"telephony.provider_call_id": call_sid},
        {"$setOnInsert": document},
        upsert=True,
    )
    if result.upserted_id is not None:
        await record_intake_creation_audit_events(audit_collection, intake_call)


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
    inserted = await record_event(collection, call_sid, "passthru", direction=direction)
    return {
        "status": "received" if inserted else "duplicate",
        "provider": "exotel",
        "call_sid": call_sid,
    }


@router.get("/exotel/voicebot", status_code=status.HTTP_200_OK)
async def exotel_voicebot_resolver(
    request: Request,
    settings: Settings = Depends(get_settings),
    encryptor: FieldEncryptor = Depends(get_field_encryptor),
    intake_collection=Depends(get_intake_calls_collection),
    audit_collection=Depends(get_intake_audit_events_collection),
) -> dict[str, str]:
    """Return the authenticated WebSocket URL for an Exotel Voicebot call."""
    _require_webhook_token(request, settings)
    params = dict(request.query_params)
    call_sid = _require_call_sid(params)
    caller_phone = _require_caller_phone(params)
    session_token = _voicebot_session_token(settings, call_sid)
    await _create_voicebot_intake_call(
        collection=intake_collection,
        audit_collection=audit_collection,
        encryptor=encryptor,
        call_sid=call_sid,
        caller_phone=caller_phone,
        session_token=session_token,
    )
    return {"url": _voicebot_stream_url(settings, session_token)}


@router.websocket("/exotel/voicebot/stream")
async def exotel_voicebot_stream(
    websocket: WebSocket,
    settings: Settings = Depends(get_settings),
    collection=Depends(get_call_events_collection),
    intake_collection=Depends(get_intake_calls_collection),
    audit_collection=Depends(get_intake_audit_events_collection),
    encryptor: FieldEncryptor = Depends(get_field_encryptor),
) -> None:
    """Accept Exotel Voicebot messages and retain audio only after consent."""
    session_token_from_auth = _voicebot_session_token_from_auth(websocket, settings)
    if session_token_from_auth is None:
        logger.warning("exotel_voicebot_authentication_rejected")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    session_token = session_token_from_auth or websocket.query_params.get("session_token", "")
    await run_voicebot_stream(
        ExotelTransport(websocket),
        session_token=session_token,
        settings=settings,
        collection=collection,
        intake_collection=intake_collection,
        audit_collection=audit_collection,
        encryptor=encryptor,
    )


async def run_voicebot_stream(
    transport: Transport,
    *,
    session_token: str,
    settings: Settings,
    collection,
    intake_collection,
    audit_collection,
    encryptor: FieldEncryptor,
) -> None:
    """Run one consent-gated intake session for an already-authenticated stream token."""
    session_token_hash = _voicebot_session_token_hash(session_token)
    intake_call = await intake_collection.find_one(
        {"voicebot_session_token_hash": session_token_hash}, {"id": 1}
    )
    if not intake_call or not intake_call.get("id"):
        logger.warning("exotel_voicebot_session_rejected")
        await transport.close(status.WS_1008_POLICY_VIOLATION)
        return

    await transport.accept()
    # Factories are looked up here, at call time, so they stay swappable on this module.
    await Session(
        transport,
        settings=settings,
        collection=collection,
        intake_collection=intake_collection,
        audit_collection=audit_collection,
        encryptor=encryptor,
        session_token_hash=session_token_hash,
        intake_call_id=UUID(str(intake_call["id"])),
        create_stt=_create_stt_session,
        create_tts=_create_tts_provider,
        create_conversation=_create_conversation_provider,
        create_storage=_create_object_storage_provider,
    ).run()


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
    inserted = await record_event(collection, call_sid, "status", **fields)
    return {
        "status": "received" if inserted else "duplicate",
        "provider": "exotel",
        "call_sid": call_sid,
    }
