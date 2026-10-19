import logging
from asyncio import Lock
from base64 import b64decode, b64encode
from binascii import Error as BinasciiError
from datetime import UTC, datetime
from hashlib import sha256
from hmac import new as hmac_new
from json import dumps
from secrets import compare_digest
from time import monotonic
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import UUID

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
from app.schemas.audit_event import IntakeAuditEventType
from app.schemas.intake_call import (
    ArtifactKind,
    CallStatus,
    CaseFact,
    IntakeCallCreateRequest,
    LanguageChoice,
    ProtectedObjectReference,
    TelephonyDetailsInput,
    TranscriptSegment,
    TranscriptSpeaker,
)
from app.security.field_encryption import FieldEncryptor, get_field_encryptor
from app.services.audit import record_audit_event, record_intake_creation_audit_events
from app.services.call_recording import ConsentGatedCallRecorder
from app.services.intake_encryption import encrypt_intake_call
from app.workflows.consent import (
    ConsentAction,
    ConsentDialogue,
    ConsentState,
    ConsentWorkflow,
    get_consent_dialogue,
)
from app.workflows.details import DetailState, DetailWorkflow
from app.workflows.language import (
    LanguageSelectionAction,
    LanguageSelectionState,
    LanguageSelectionWorkflow,
)

router = APIRouter()
logger = logging.getLogger(__name__)

LANGUAGE_SELECTION_PROMPT = (
    "Hello. I am an AI assistant for the law firm. Please say English or Hindi to choose your "
    "language."
)
HINDI_LANGUAGE_SELECTION_PROMPT = (
    "नमस्ते। मैं लॉ फर्म की एआई सहायक हूँ। अपनी भाषा चुनने के लिए कृपया अंग्रेज़ी या हिंदी कहें।"
)
LANGUAGE_SELECTION_REPROMPT = "Please say English or Hindi."
HINDI_LANGUAGE_SELECTION_REPROMPT = "कृपया अंग्रेज़ी या हिंदी कहें।"
LANGUAGE_SELECTION_UNCLEAR_CLOSURE = (
    "I was unable to confirm your language choice, so I will not collect case information. "
    "Thank you for calling. Goodbye."
)
HINDI_LANGUAGE_SELECTION_UNCLEAR_CLOSURE = (
    "मैं आपकी भाषा की पुष्टि नहीं कर सकी, इसलिए मामले की जानकारी नहीं लूँगी। धन्यवाद। नमस्ते।"
)

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


def _create_object_storage_provider(settings: Settings) -> ObjectStorageProvider:
    return GoogleCloudStorageProvider.from_settings(settings)


def _speaker_for_language(settings: Settings, language: LanguageChoice) -> str:
    return {
        LanguageChoice.ENGLISH: settings.sarvam_tts_speaker,
        LanguageChoice.HINDI: settings.sarvam_tts_hindi_speaker,
    }[language]


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
    collection, stream_sid: str | None, event_type: str, **fields: object
) -> None:
    """Record Voicebot lifecycle metadata without retaining media or transcript content."""
    if not stream_sid:
        logger.warning("exotel_voicebot_%s_missing_stream_sid", event_type)
        return
    try:
        await _record_event(collection, stream_sid, event_type, stream_sid=stream_sid, **fields)
    except Exception:
        logger.exception(
            "exotel_voicebot_%s_event_record_failed stream_sid=%s", event_type, stream_sid
        )


async def _create_voicebot_intake_call(
    *,
    collection,
    audit_collection,
    encryptor: FieldEncryptor,
    call_sid: str,
    caller_phone: str,
    session_token: str,
) -> None:
    intake_call = encrypt_intake_call(
        IntakeCallCreateRequest(
            status=CallStatus.CONSENT_PENDING,
            telephony=TelephonyDetailsInput(
                provider="exotel",
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


async def _update_voicebot_intake_call(
    collection,
    session_token_hash: str,
    **fields: object,
) -> None:
    result = await collection.update_one(
        {"voicebot_session_token_hash": session_token_hash},
        {"$set": {**fields, "updated_at": datetime.now(UTC)}},
    )
    if result.matched_count != 1:
        logger.warning("voicebot_intake_update_not_matched")


async def _attach_voicebot_recording_artifact(
    collection,
    session_token_hash: str,
    artifact: ProtectedObjectReference,
) -> bool:
    result = await collection.update_one(
        {"voicebot_session_token_hash": session_token_hash},
        {
            "$push": {"intake.artifacts": artifact.model_dump(mode="json")},
            "$set": {"updated_at": datetime.now(UTC)},
        },
    )
    if result.matched_count != 1:
        logger.warning("voicebot_recording_artifact_not_matched")
        return False
    return True


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
    session_token_hash = _voicebot_session_token_hash(session_token)
    intake_call = await intake_collection.find_one(
        {"voicebot_session_token_hash": session_token_hash}, {"id": 1}
    )
    if not intake_call or not intake_call.get("id"):
        logger.warning("exotel_voicebot_session_rejected")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    intake_call_id = UUID(str(intake_call["id"]))

    await websocket.accept()
    stream_sid: str | None = None
    stt_session: StreamingSpeechToTextSession | None = None
    language_workflow = LanguageSelectionWorkflow()
    consent_workflow = ConsentWorkflow()
    speech_lock = Lock()
    selected_dialogue: ConsentDialogue | None = None
    recording_provider: ObjectStorageProvider | None = None
    recorder: ConsentGatedCallRecorder | None = None
    recording_finalized = False
    detail_workflow: DetailWorkflow | None = None
    stream_started_at = monotonic()

    async def speak(
        text: str,
        *,
        language_code: str | None = None,
        speaker: str | None = None,
    ) -> None:
        async with speech_lock:
            tts_provider = _create_tts_provider(settings)
            async for audio in tts_provider.stream(
                text, language_code=language_code, speaker=speaker
            ):
                if recorder:
                    recorder.append_agent_audio(audio.content)
                await websocket.send_json(
                    {
                        "event": "media",
                        "stream_sid": stream_sid,
                        "media": {"payload": b64encode(audio.content).decode()},
                    }
                )

    async def on_transcript(event_type: str, transcript: str, language: str | None) -> None:
        nonlocal detail_workflow, recording_provider, recorder, selected_dialogue
        logger.info(
            "sarvam_stt_transcript_received stream_sid=%s event=%s language=%s characters=%d",
            stream_sid,
            event_type,
            language or "unknown",
            len(transcript),
        )
        if event_type != "transcript.final":
            return
        if language_workflow.state == LanguageSelectionState.AWAITING_SELECTION:
            action = language_workflow.handle_response(transcript)
            if action == LanguageSelectionAction.REPROMPT_LANGUAGE:
                await _record_voicebot_session_event(
                    collection, stream_sid, "voicebot_reprompt_language"
                )
                await speak(
                    LANGUAGE_SELECTION_REPROMPT,
                    language_code="en-IN",
                    speaker=_speaker_for_language(settings, LanguageChoice.ENGLISH),
                )
                await speak(
                    HINDI_LANGUAGE_SELECTION_REPROMPT,
                    language_code="hi-IN",
                    speaker=_speaker_for_language(settings, LanguageChoice.HINDI),
                )
                return
            if action == LanguageSelectionAction.CLOSE_CALL:
                await _record_voicebot_session_event(collection, stream_sid, "voicebot_close_call")
                await _update_voicebot_intake_call(
                    intake_collection,
                    session_token_hash,
                    status=CallStatus.ABANDONED.value,
                )
                await speak(
                    LANGUAGE_SELECTION_UNCLEAR_CLOSURE,
                    language_code="en-IN",
                    speaker=_speaker_for_language(settings, LanguageChoice.ENGLISH),
                )
                await speak(
                    HINDI_LANGUAGE_SELECTION_UNCLEAR_CLOSURE,
                    language_code="hi-IN",
                    speaker=_speaker_for_language(settings, LanguageChoice.HINDI),
                )
                return

            selected_language = language_workflow.selected_language
            if selected_language is None:
                raise RuntimeError("Language selection completed without a selected language")
            selected_dialogue = get_consent_dialogue(selected_language)
            await _record_voicebot_session_event(
                collection,
                stream_sid,
                "voicebot_language_selected",
                language=selected_language,
            )
            await _update_voicebot_intake_call(
                intake_collection,
                session_token_hash,
                **{
                    "language.preferred": selected_language.value,
                    "disclosures.recording_disclosure_given_at": datetime.now(UTC),
                    "disclosures.no_advice_disclosure_given_at": datetime.now(UTC),
                    "disclosures.no_engagement_disclosure_given_at": datetime.now(UTC),
                },
            )
            consent_workflow.begin()
            await speak(
                selected_dialogue.prompt,
                language_code=selected_dialogue.language_code,
                speaker=_speaker_for_language(settings, selected_language),
            )
            return
        if consent_workflow.state == ConsentState.GRANTED and detail_workflow:
            if detail_workflow.state == DetailState.COMPLETE:
                return
            # Sarvam's current callback has no segment timing or confidence. Record the
            # receipt time instead of inventing a speech duration or confidence score.
            received_ms = max(0, int((monotonic() - stream_started_at) * 1000))
            segment = TranscriptSegment(
                speaker=TranscriptSpeaker.CALLER,
                text_encrypted=encryptor.encrypt(transcript),
                started_at_ms=received_ms,
                ended_at_ms=received_ms,
                detected_language=language,
            )
            result = await intake_collection.update_one(
                {
                    "voicebot_session_token_hash": session_token_hash,
                    "disclosures.recording_consent": True,
                    "disclosures.intake_consent": True,
                },
                {
                    "$push": {"intake.transcript_segments": segment.model_dump(mode="json")},
                    "$set": {"updated_at": datetime.now(UTC)},
                },
            )
            if result.matched_count != 1:
                logger.error("voicebot_transcript_consent_or_session_not_matched")
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                return
            detail_result = detail_workflow.handle_response(transcript, str(segment.id))
            if detail_result.field and detail_result.value and detail_result.source_segment_id:
                fact = CaseFact(
                    field_name=detail_result.field,
                    value_encrypted=encryptor.encrypt(detail_result.value),
                    source_transcript_segment_ids=[UUID(detail_result.source_segment_id)],
                )
                result = await intake_collection.update_one(
                    {
                        "voicebot_session_token_hash": session_token_hash,
                        "disclosures.recording_consent": True,
                        "disclosures.intake_consent": True,
                    },
                    {
                        "$push": {"intake.facts": fact.model_dump(mode="json")},
                        "$set": {"updated_at": datetime.now(UTC)},
                    },
                )
                if result.matched_count != 1:
                    logger.error("voicebot_fact_consent_or_session_not_matched")
                    await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                    return
            await speak(
                detail_result.prompt,
                language_code=selected_dialogue.language_code,
                speaker=_speaker_for_language(settings, detail_workflow.language),
            )
            return
        if consent_workflow.state != ConsentState.AWAITING_CONSENT:
            logger.info(
                "consent_transcript_ignored stream_sid=%s state=%s",
                stream_sid,
                consent_workflow.state,
            )
            return

        action = consent_workflow.handle_response(transcript)
        if selected_dialogue is None:
            raise RuntimeError("Consent response received without a selected language")
        response = {
            ConsentAction.REPROMPT_CONSENT: selected_dialogue.reprompt,
            ConsentAction.CONSENT_GRANTED: selected_dialogue.granted_response,
            ConsentAction.CONSENT_DECLINED: selected_dialogue.declined_response,
            ConsentAction.CLOSE_CALL: selected_dialogue.unclear_closure,
        }[action]
        await _record_voicebot_session_event(collection, stream_sid, f"voicebot_{action}")
        if action == ConsentAction.CONSENT_GRANTED:
            consent_value: bool | None = True
            call_status = CallStatus.IN_PROGRESS
        elif action == ConsentAction.CONSENT_DECLINED:
            consent_value = False
            call_status = CallStatus.CONSENT_DECLINED
        elif action == ConsentAction.CLOSE_CALL:
            consent_value = None
            call_status = CallStatus.ABANDONED
        else:
            consent_value = None
            call_status = None
        if call_status:
            consent_fields: dict[str, object] = {"status": call_status.value}
            if consent_value is not None:
                consent_fields.update(
                    {
                        "disclosures.recording_consent": consent_value,
                        "disclosures.recording_consent_at": datetime.now(UTC),
                        "disclosures.intake_consent": consent_value,
                        "disclosures.intake_consent_at": datetime.now(UTC),
                    }
                )
            await _update_voicebot_intake_call(
                intake_collection, session_token_hash, **consent_fields
            )
            if consent_value is not None:
                await record_audit_event(
                    audit_collection,
                    intake_call_id=intake_call_id,
                    event_type=IntakeAuditEventType.CONSENT_RECORDED,
                    details={"intake_consent": consent_value},
                )
            if (
                action == ConsentAction.CONSENT_GRANTED
                and settings.object_storage_provider == "gcs"
            ):
                try:
                    recording_provider = _create_object_storage_provider(settings)
                    recorder = ConsentGatedCallRecorder(settings.sarvam_stt_sample_rate)
                    await _update_voicebot_intake_call(
                        intake_collection,
                        session_token_hash,
                        **{"disclosures.recording_started_at": datetime.now(UTC)},
                    )
                    await _record_voicebot_session_event(
                        collection, stream_sid, "voicebot_recording_started"
                    )
                except (OSError, ValueError):
                    logger.exception("voicebot_recording_start_failed stream_sid=%s", stream_sid)
                    await _record_voicebot_session_event(
                        collection, stream_sid, "voicebot_recording_start_failed"
                    )
        try:
            if action == ConsentAction.CONSENT_GRANTED:
                detail_workflow = DetailWorkflow(language_workflow.selected_language)
                response = f"{response} {detail_workflow.begin()}"
            await speak(
                response,
                language_code=selected_dialogue.language_code,
                speaker=_speaker_for_language(settings, language_workflow.selected_language),
            )
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
        if language_workflow.begin() != LanguageSelectionAction.REQUEST_LANGUAGE:
            return
        await speak(
            LANGUAGE_SELECTION_PROMPT,
            language_code="en-IN",
            speaker=_speaker_for_language(settings, LanguageChoice.ENGLISH),
        )
        await speak(
            HINDI_LANGUAGE_SELECTION_PROMPT,
            language_code="hi-IN",
            speaker=_speaker_for_language(settings, LanguageChoice.HINDI),
        )

    async def finalize_recording() -> None:
        nonlocal recording_finalized
        if recording_finalized or not recording_provider or not recorder:
            return
        recording_finalized = True
        stored_any_track = False
        for track in recorder.to_wav_tracks():
            try:
                stored_object = await recording_provider.store(
                    intake_call_id,
                    track.filename,
                    track.content,
                    "audio/wav",
                )
                artifact = ProtectedObjectReference(
                    kind=ArtifactKind.CALL_RECORDING,
                    storage_provider="gcs",
                    storage_key=stored_object.key,
                    content_type=stored_object.content_type,
                )
                if await _attach_voicebot_recording_artifact(
                    intake_collection, session_token_hash, artifact
                ):
                    stored_any_track = True
                    await record_audit_event(
                        audit_collection,
                        intake_call_id=intake_call_id,
                        event_type=IntakeAuditEventType.ARTIFACT_ATTACHED,
                        details={"artifact_kind": artifact.kind.value},
                    )
                    await _record_voicebot_session_event(
                        collection, stream_sid, "voicebot_recording_stored", track=track.filename
                    )
            except (OSError, ValueError):
                logger.exception("voicebot_recording_store_failed stream_sid=%s", stream_sid)
                await _record_voicebot_session_event(
                    collection, stream_sid, "voicebot_recording_store_failed", track=track.filename
                )
        if stored_any_track:
            await _update_voicebot_intake_call(
                intake_collection,
                session_token_hash,
                **{"disclosures.recording_stopped_at": datetime.now(UTC)},
            )

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
                    await _update_voicebot_intake_call(
                        intake_collection,
                        session_token_hash,
                        **{"disclosures.ai_identity_given_at": datetime.now(UTC)},
                    )
                    await speak_consent_prompt()
                except (OSError, ValueError):
                    logger.exception("voicebot_session_start_failed stream_sid=%s", stream_sid)
                    await _record_voicebot_session_event(
                        collection, stream_sid, "voicebot_stt_start_failed"
                    )
                    await websocket.close(code=status.WS_1011_INTERNAL_ERROR)
                    return
            elif event_type == "media":
                payload = message.get("media", {}).get("payload")
                if isinstance(payload, str) and payload:
                    if recorder:
                        try:
                            recorder.append_caller_payload(payload)
                        except ValueError:
                            logger.warning(
                                "voicebot_recording_invalid_audio stream_sid=%s", stream_sid
                            )
                    if stt_session and (
                        language_workflow.state == LanguageSelectionState.AWAITING_SELECTION
                        or consent_workflow.state == ConsentState.AWAITING_CONSENT
                        or (
                            consent_workflow.state == ConsentState.GRANTED
                            and detail_workflow is not None
                            and detail_workflow.state != DetailState.COMPLETE
                        )
                    ):
                        await stt_session.send_audio(payload)
            elif event_type == "stop":
                logger.info("exotel_voicebot_stream_stopped stream_sid=%s", stream_sid)
                await _record_voicebot_session_event(
                    collection, stream_sid, "voicebot_stream_stopped"
                )
                if stt_session:
                    await stt_session.stop()
                await finalize_recording()
                await websocket.close()
                return
    except WebSocketDisconnect:
        logger.info("exotel_voicebot_stream_disconnected stream_sid=%s", stream_sid)
        await _record_voicebot_session_event(collection, stream_sid, "voicebot_stream_disconnected")
    finally:
        if stt_session:
            await stt_session.stop()
        await finalize_recording()


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
