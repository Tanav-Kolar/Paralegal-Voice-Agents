import asyncio
import logging
from base64 import b64encode
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID

from fastapi import WebSocketDisconnect, status

from app.core.config import Settings
from app.providers.contracts import (
    ObjectStorageProvider,
    StreamingSpeechToTextSession,
    StreamingTextToSpeechProvider,
    TranscriptHandler,
)
from app.schemas.audit_event import IntakeAuditEventType
from app.schemas.intake_call import (
    ArtifactKind,
    CallStatus,
    CaseFact,
    LanguageChoice,
    ProtectedObjectReference,
    TranscriptSegment,
    TranscriptSpeaker,
)
from app.security.field_encryption import FieldEncryptor
from app.services.audit import record_audit_event
from app.services.call_events import record_event
from app.services.call_recording import ConsentGatedCallRecorder
from app.services.extraction import extract_facts
from app.voice.transport import Audio, Start, Stop, Transport
from app.workflows.consent import (
    ConsentAction,
    ConsentDialogue,
    ConsentState,
    ConsentWorkflow,
    get_consent_dialogue,
)
from app.workflows.conversation import (
    ConversationProvider,
    ConversationUnavailable,
    ConversationWorkflow,
)
from app.workflows.language import (
    LanguageSelectionAction,
    LanguageSelectionState,
    LanguageSelectionWorkflow,
)

logger = logging.getLogger(__name__)

LANGUAGE_SELECTION_PROMPT = (
    "Hello. To speak in English, say English."
)
HINDI_LANGUAGE_SELECTION_PROMPT = (
    "हिंदी में बात करने के लिए हिंदी कहें।"
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
CONVERSATION_UNAVAILABLE = {
    LanguageChoice.ENGLISH: (
        "I am unable to continue the intake right now. Thank you for calling. Goodbye."
    ),
    LanguageChoice.HINDI: "मैं अभी इंटेक आगे नहीं बढ़ा सकती। कॉल करने के लिए धन्यवाद। नमस्ते।",
}


def speaker_for_language(settings: Settings, language: LanguageChoice) -> str:
    return {
        LanguageChoice.ENGLISH: settings.sarvam_tts_speaker,
        LanguageChoice.HINDI: settings.sarvam_tts_hindi_speaker,
    }[language]


async def record_voicebot_session_event(
    collection, stream_sid: str | None, event_type: str, provider: str, **fields: object
) -> None:
    """Record Voicebot lifecycle metadata without retaining media or transcript content."""
    if not stream_sid:
        logger.warning("exotel_voicebot_%s_missing_stream_sid", event_type)
        return
    try:
        await record_event(
            collection, stream_sid, event_type, provider=provider, stream_sid=stream_sid, **fields
        )
    except Exception:
        logger.exception(
            "exotel_voicebot_%s_event_record_failed stream_sid=%s", event_type, stream_sid
        )


async def update_voicebot_intake_call(
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


async def attach_voicebot_recording_artifact(
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


class Session:
    """One consent-gated intake call: language, consent, conversation, recording, persistence.

    The wire is a Transport; providers come in as factories so callers choose (and tests swap) them.
    """

    def __init__(
        self,
        transport: Transport,
        *,
        settings: Settings,
        collection,
        intake_collection,
        audit_collection,
        encryptor: FieldEncryptor,
        session_token_hash: str,
        intake_call_id: UUID,
        create_stt: Callable[[Settings, TranscriptHandler], StreamingSpeechToTextSession],
        create_tts: Callable[[Settings], StreamingTextToSpeechProvider],
        create_conversation: Callable[[Settings], ConversationProvider],
        create_storage: Callable[[Settings], ObjectStorageProvider],
    ) -> None:
        self.transport = transport
        self.settings = settings
        self.collection = collection
        self.intake_collection = intake_collection
        self.audit_collection = audit_collection
        self.encryptor = encryptor
        self.session_token_hash = session_token_hash
        self.intake_call_id = intake_call_id
        self._create_stt = create_stt
        self._create_tts = create_tts
        self._create_conversation = create_conversation
        self._create_storage = create_storage

        self.stream_sid: str | None = None
        self.stt_session: StreamingSpeechToTextSession | None = None
        self.language_workflow = LanguageSelectionWorkflow()
        self.consent_workflow = ConsentWorkflow()
        self.speech_lock = asyncio.Lock()
        # Turns (and the opening prompt, as None) run on a worker so the STT receiver and the
        # media loop keep moving while the agent speaks; that is what makes barge-in possible.
        self._turns: asyncio.Queue[tuple[str, str | None] | None] = asyncio.Queue()
        self._worker: asyncio.Task | None = None
        self._speech_task: asyncio.Task | None = None
        self._turn_interrupted = False
        self._hung_up = False
        self._sent_audio = False
        self.selected_dialogue: ConsentDialogue | None = None
        self.recording_provider: ObjectStorageProvider | None = None
        self.recorder: ConsentGatedCallRecorder | None = None
        self.recording_finalized = False
        self.conversation: ConversationWorkflow | None = None
        self.started_at = monotonic()

    def _speaker(self, language: LanguageChoice) -> str:
        return speaker_for_language(self.settings, language)

    async def _session_event(self, event_type: str, **fields: object) -> None:
        await record_voicebot_session_event(
            self.collection, self.stream_sid, event_type, self.transport.provider, **fields
        )

    async def _update_call(self, **fields: object) -> None:
        await update_voicebot_intake_call(self.intake_collection, self.session_token_hash, **fields)

    async def speak(
        self,
        text: str,
        *,
        language_code: str | None = None,
        speaker: str | None = None,
    ) -> None:
        if self._turn_interrupted or self._hung_up:
            return
        async with self.speech_lock:
            self._sent_audio = False
            await self.transport.send_transcript("agent", text)
            self._speech_task = asyncio.create_task(
                self._stream_speech(text, language_code, speaker)
            )
            try:
                await self._speech_task
            except asyncio.CancelledError:
                # Only swallow our own interrupt; a cancel aimed at this task must propagate.
                if asyncio.current_task().cancelling():
                    raise
            finally:
                self._speech_task = None
            if self._turn_interrupted and not self._sent_audio:
                return
            await self._save_agent_segment(text, language_code)

    async def _stream_speech(
        self, text: str, language_code: str | None, speaker: str | None
    ) -> None:
        tts_provider = self._create_tts(self.settings)
        leftover = b""
        async for audio in tts_provider.stream(text, language_code=language_code, speaker=speaker):
            # Keep every frame sample-aligned; a split 16-bit sample plays as static.
            chunk = leftover + audio.content
            usable = len(chunk) - len(chunk) % 2
            chunk, leftover = chunk[:usable], chunk[usable:]
            if not chunk:
                continue
            if self.recorder:
                self.recorder.append_agent_audio(chunk)
            await self.transport.send_audio(chunk)
            self._sent_audio = True

    async def _save_agent_segment(self, text: str, language_code: str | None) -> None:
        if self.consent_workflow.state != ConsentState.GRANTED or not self.conversation:
            return
        received_ms = max(0, int((monotonic() - self.started_at) * 1000))
        agent_segment = TranscriptSegment(
            speaker=TranscriptSpeaker.AGENT,
            text_encrypted=self.encryptor.encrypt(text),
            started_at_ms=received_ms,
            ended_at_ms=received_ms,
            detected_language=language_code,
        )
        result = await self.intake_collection.update_one(
            {
                "voicebot_session_token_hash": self.session_token_hash,
                "disclosures.recording_consent": True,
                "disclosures.intake_consent": True,
            },
            {
                "$push": {"intake.transcript_segments": agent_segment.model_dump(mode="json")},
                "$set": {"updated_at": datetime.now(UTC)},
            },
        )
        if result.matched_count != 1:
            raise RuntimeError("Could not save the consented agent transcript")

    async def on_transcript(self, event_type: str, transcript: str, language: str | None) -> None:
        # Called from the STT receiver task, so it must return quickly: finals are queued for the
        # turn worker, and a partial may interrupt the agent mid-sentence (barge-in).
        logger.info(
            "sarvam_stt_transcript_received stream_sid=%s event=%s language=%s characters=%d",
            self.stream_sid,
            event_type,
            language or "unknown",
            len(transcript),
        )
        barge_in_enabled = (
            self.settings.demo_barge_in_enabled
            if self.transport.provider == "demo"
            else self.settings.voice_barge_in_enabled
        )
        if event_type == "transcript.partial" and barge_in_enabled:
            await self._interrupt_speech()
        elif event_type == "transcript.final":
            await self.transport.send_transcript("caller", transcript)
            self._turns.put_nowait((transcript, language))

    async def _interrupt_speech(self) -> None:
        task = self._speech_task
        if self._turn_interrupted or not task or task.done():
            return
        self._turn_interrupted = True  # also drops the rest of this turn's planned speech
        task.cancel()
        await self._session_event(
            "voicebot_agent_interrupted",
            at_ms=max(0, int((monotonic() - self.started_at) * 1000)),
        )
        with suppress(RuntimeError):
            await self.transport.clear()

    async def _turn_worker(self) -> None:
        while True:
            item = await self._turns.get()
            self._turn_interrupted = False
            try:
                if item is None:
                    await self._greet()
                else:
                    await self._handle_transcript(*item)
            except Exception:
                # An escaped error would leave a live call that never answers; end it instead.
                logger.exception("voicebot_turn_failed stream_sid=%s", self.stream_sid)
                await self._session_event("voicebot_turn_failed")
                with suppress(RuntimeError):
                    await self.transport.close(status.WS_1011_INTERNAL_ERROR)
            finally:
                self._turns.task_done()

    async def _greet(self) -> None:
        try:
            await self._speak_language_prompt()
        except (OSError, ValueError):
            logger.exception("voicebot_session_start_failed stream_sid=%s", self.stream_sid)
            await self._session_event("voicebot_stt_start_failed")
            await self.transport.close(status.WS_1011_INTERNAL_ERROR)

    async def _drain_turns(self) -> None:
        with suppress(TimeoutError):
            await asyncio.wait_for(self._turns.join(), timeout=10)

    async def _handle_transcript(self, transcript: str, language: str | None) -> None:
        if self.language_workflow.state == LanguageSelectionState.AWAITING_SELECTION:
            await self._handle_language_response(transcript)
            return
        if self.consent_workflow.state == ConsentState.GRANTED and self.conversation:
            await self._handle_conversation_response(transcript, language)
            return
        if self.consent_workflow.state != ConsentState.AWAITING_CONSENT:
            logger.info(
                "consent_transcript_ignored stream_sid=%s state=%s",
                self.stream_sid,
                self.consent_workflow.state,
            )
            return
        await self._handle_consent_response(transcript)

    async def _handle_language_response(self, transcript: str) -> None:
        action = self.language_workflow.handle_response(transcript)
        if action == LanguageSelectionAction.REPROMPT_LANGUAGE:
            await self._session_event("voicebot_reprompt_language")
            await self.speak(
                LANGUAGE_SELECTION_REPROMPT,
                language_code="en-IN",
                speaker=self._speaker(LanguageChoice.ENGLISH),
            )
            await self.speak(
                HINDI_LANGUAGE_SELECTION_REPROMPT,
                language_code="hi-IN",
                speaker=self._speaker(LanguageChoice.HINDI),
            )
            return
        if action == LanguageSelectionAction.CLOSE_CALL:
            await self._session_event("voicebot_close_call")
            await self._update_call(status=CallStatus.ABANDONED.value)
            await self.speak(
                LANGUAGE_SELECTION_UNCLEAR_CLOSURE,
                language_code="en-IN",
                speaker=self._speaker(LanguageChoice.ENGLISH),
            )
            await self.speak(
                HINDI_LANGUAGE_SELECTION_UNCLEAR_CLOSURE,
                language_code="hi-IN",
                speaker=self._speaker(LanguageChoice.HINDI),
            )
            return

        selected_language = self.language_workflow.selected_language
        if selected_language is None:
            raise RuntimeError("Language selection completed without a selected language")
        self.selected_dialogue = get_consent_dialogue(selected_language)
        await self._session_event("voicebot_language_selected", language=selected_language)
        await self._update_call(
            **{
                "language.preferred": selected_language.value,
                "disclosures.recording_disclosure_given_at": datetime.now(UTC),
                "disclosures.no_advice_disclosure_given_at": datetime.now(UTC),
                "disclosures.no_engagement_disclosure_given_at": datetime.now(UTC),
            }
        )
        self.consent_workflow.begin()
        await self.speak(
            self.selected_dialogue.prompt,
            language_code=self.selected_dialogue.language_code,
            speaker=self._speaker(selected_language),
        )

    async def _handle_conversation_response(self, transcript: str, language: str | None) -> None:
        conversation = self.conversation
        dialogue = self.selected_dialogue
        if conversation.finished:
            return
        # Sarvam's current callback has no segment timing or confidence. Record the
        # receipt time instead of inventing a speech duration or confidence score.
        received_ms = max(0, int((monotonic() - self.started_at) * 1000))
        segment = TranscriptSegment(
            speaker=TranscriptSpeaker.CALLER,
            text_encrypted=self.encryptor.encrypt(transcript),
            started_at_ms=received_ms,
            ended_at_ms=received_ms,
            detected_language=language,
        )
        result = await self.intake_collection.update_one(
            {
                "voicebot_session_token_hash": self.session_token_hash,
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
            await self.transport.close(status.WS_1011_INTERNAL_ERROR)
            return
        try:
            conversation_reply = await conversation.handle_response(transcript)
        except ConversationUnavailable:
            conversation.finished = True
            await self._session_event("voicebot_conversation_unavailable")
            await self._update_call(status=CallStatus.ABANDONED.value)
            await self.speak(
                CONVERSATION_UNAVAILABLE[conversation.language],
                language_code=dialogue.language_code,
                speaker=self._speaker(conversation.language),
            )
            return
        await self.speak(
            conversation_reply.reply,
            language_code=dialogue.language_code,
            speaker=self._speaker(conversation.language),
        )
        if conversation.finished:
            await self._update_call(status=CallStatus.COMPLETED.value)

    async def _handle_consent_response(self, transcript: str) -> None:
        action = self.consent_workflow.handle_response(transcript)
        dialogue = self.selected_dialogue
        if dialogue is None:
            raise RuntimeError("Consent response received without a selected language")
        response = {
            ConsentAction.REPROMPT_CONSENT: dialogue.reprompt,
            ConsentAction.CONSENT_GRANTED: dialogue.granted_response,
            ConsentAction.CONSENT_DECLINED: dialogue.declined_response,
            ConsentAction.CLOSE_CALL: dialogue.unclear_closure,
        }[action]
        await self._session_event(f"voicebot_{action}")
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
            await self._update_call(**consent_fields)
            if consent_value is not None:
                await record_audit_event(
                    self.audit_collection,
                    intake_call_id=self.intake_call_id,
                    event_type=IntakeAuditEventType.CONSENT_RECORDED,
                    details={"intake_consent": consent_value},
                )
            if (
                action == ConsentAction.CONSENT_GRANTED
                and self.settings.object_storage_provider == "gcs"
            ):
                await self._start_recording()
        try:
            if action == ConsentAction.CONSENT_GRANTED:
                try:
                    self.conversation = ConversationWorkflow(
                        self.language_workflow.selected_language,
                        self._create_conversation(self.settings),
                    )
                    first_reply = await self.conversation.begin()
                    response = f"{response} {first_reply.reply}"
                except ConversationUnavailable:
                    if self.conversation:
                        self.conversation.finished = True
                    await self._session_event("voicebot_conversation_unavailable")
                    await self._update_call(status=CallStatus.ABANDONED.value)
                    response = CONVERSATION_UNAVAILABLE[self.language_workflow.selected_language]
            await self.speak(
                response,
                language_code=dialogue.language_code,
                speaker=self._speaker(self.language_workflow.selected_language),
            )
        except (OSError, ValueError):
            logger.exception(
                "sarvam_tts_response_failed stream_sid=%s action=%s", self.stream_sid, action
            )
            await self._session_event("voicebot_tts_response_failed")
            await self.transport.close(status.WS_1011_INTERNAL_ERROR)

    async def _start_recording(self) -> None:
        try:
            self.recording_provider = self._create_storage(self.settings)
            self.recorder = ConsentGatedCallRecorder(
                self.settings.sarvam_stt_sample_rate, self.settings.sarvam_tts_sample_rate
            )
            await self._update_call(**{"disclosures.recording_started_at": datetime.now(UTC)})
            await self._session_event("voicebot_recording_started")
        except (OSError, ValueError):
            logger.exception("voicebot_recording_start_failed stream_sid=%s", self.stream_sid)
            await self._session_event("voicebot_recording_start_failed")

    async def _speak_language_prompt(self) -> None:
        if self.language_workflow.begin() != LanguageSelectionAction.REQUEST_LANGUAGE:
            return
        await self.speak(
            LANGUAGE_SELECTION_PROMPT,
            language_code="en-IN",
            speaker=self._speaker(LanguageChoice.ENGLISH),
        )
        await self.speak(
            HINDI_LANGUAGE_SELECTION_PROMPT,
            language_code="hi-IN",
            speaker=self._speaker(LanguageChoice.HINDI),
        )

    async def _finalize_recording(self) -> None:
        if self.recording_finalized or not self.recording_provider or not self.recorder:
            return
        self.recording_finalized = True
        track = self.recorder.to_wav_track()
        if not track:
            return
        try:
            stored_object = await self.recording_provider.store(
                self.intake_call_id, track.filename, track.content, "audio/wav"
            )
            artifact = ProtectedObjectReference(
                kind=ArtifactKind.CALL_RECORDING,
                storage_provider="gcs",
                storage_key=stored_object.key,
                content_type=stored_object.content_type,
            )
            stored = await attach_voicebot_recording_artifact(
                self.intake_collection, self.session_token_hash, artifact
            )
            if not stored:
                return
            await record_audit_event(
                self.audit_collection,
                intake_call_id=self.intake_call_id,
                event_type=IntakeAuditEventType.ARTIFACT_ATTACHED,
                details={"artifact_kind": artifact.kind.value},
            )
            await self._session_event("voicebot_recording_stored", track=track.filename)
            await self._update_call(**{"disclosures.recording_stopped_at": datetime.now(UTC)})
        except (OSError, ValueError):
            logger.exception("voicebot_recording_store_failed stream_sid=%s", self.stream_sid)
            await self._session_event("voicebot_recording_store_failed", track=track.filename)

    async def _extract_facts(self) -> None:
        """Best effort: a failure here must never lose the call, so it only logs."""
        if self.consent_workflow.state != ConsentState.GRANTED or not self.conversation:
            return
        try:
            document = await self.intake_collection.find_one(
                {"voicebot_session_token_hash": self.session_token_hash},
                {"intake.transcript_segments": 1},
            )
            segments = [
                (UUID(str(s["id"])), s["speaker"], self.encryptor.decrypt(s["text_encrypted"]))
                for s in (document or {}).get("intake", {}).get("transcript_segments", [])
            ]
            if not any(speaker == "caller" for _, speaker, _ in segments):
                return
            provider = self._create_conversation(self.settings)
            facts = await extract_facts(provider.complete_json, segments)
            stored = [
                CaseFact(
                    field_name=f.field_name,
                    value_encrypted=self.encryptor.encrypt(f.value) if f.value else None,
                    source_transcript_segment_ids=f.source_transcript_segment_ids,
                    not_provided=f.not_provided,
                ).model_dump(mode="json")
                for f in facts
            ]
            await self.intake_collection.update_one(
                {"voicebot_session_token_hash": self.session_token_hash},
                {"$set": {"intake.facts": stored, "updated_at": datetime.now(UTC)}},
            )
            await self._session_event("voicebot_facts_extracted", count=len(stored))
        except Exception:
            logger.exception("voicebot_fact_extraction_failed stream_sid=%s", self.stream_sid)

    def _wants_caller_audio(self) -> bool:
        return (
            self.language_workflow.state == LanguageSelectionState.AWAITING_SELECTION
            or self.consent_workflow.state == ConsentState.AWAITING_CONSENT
            or (
                self.consent_workflow.state == ConsentState.GRANTED
                and self.conversation is not None
                and not self.conversation.finished
            )
        )

    async def run(self) -> None:
        try:
            while True:
                event = await self.transport.recv()
                if isinstance(event, Start):
                    if self.stt_session:
                        logger.warning(
                            "voicebot_duplicate_start_ignored stream_sid=%s", self.stream_sid
                        )
                        continue
                    self.stream_sid = event.stream_sid
                    await self._update_call(**{"telephony.stream_sid": self.stream_sid})
                    logger.info("exotel_voicebot_stream_started stream_sid=%s", self.stream_sid)
                    await self._session_event("voicebot_stream_started")
                    try:
                        self.stt_session = self._create_stt(self.settings, self.on_transcript)
                        await self.stt_session.start()
                        await self._update_call(
                            **{"disclosures.ai_identity_given_at": datetime.now(UTC)}
                        )
                    except (OSError, ValueError):
                        logger.exception(
                            "voicebot_session_start_failed stream_sid=%s", self.stream_sid
                        )
                        await self._session_event("voicebot_stt_start_failed")
                        await self.transport.close(status.WS_1011_INTERNAL_ERROR)
                        return
                    self._worker = asyncio.create_task(self._turn_worker())
                    self._turns.put_nowait(None)
                elif isinstance(event, Audio):
                    if self.recorder:
                        self.recorder.append_caller_audio(event.pcm)
                    if self.stt_session and self._wants_caller_audio():
                        await self.stt_session.send_audio(b64encode(event.pcm).decode())
                elif isinstance(event, Stop):
                    logger.info("exotel_voicebot_stream_stopped stream_sid=%s", self.stream_sid)
                    await self._session_event("voicebot_stream_stopped")
                    if self.stt_session:
                        await self.stt_session.stop()
                    # The caller is gone: stop speaking but still finish persisting queued turns.
                    self._hung_up = True
                    if self._speech_task:
                        self._speech_task.cancel()
                    await self._drain_turns()
                    await self._finalize_recording()
                    await self.transport.close()
                    return
        except WebSocketDisconnect:
            logger.info("exotel_voicebot_stream_disconnected stream_sid=%s", self.stream_sid)
            await self._session_event("voicebot_stream_disconnected")
        finally:
            if self._worker:
                self._worker.cancel()
                await asyncio.gather(self._worker, return_exceptions=True)
            if self.stt_session:
                await self.stt_session.stop()
            await self._finalize_recording()
            await self._extract_facts()
