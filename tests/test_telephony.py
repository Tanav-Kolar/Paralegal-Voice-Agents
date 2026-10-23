from base64 import b64decode, b64encode
from io import BytesIO
from urllib.parse import urlsplit
from wave import open as wave_open

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.routes.telephony import HINDI_LANGUAGE_SELECTION_PROMPT, LANGUAGE_SELECTION_PROMPT
from app.core.config import Settings, get_settings
from app.db.mongodb import (
    get_call_events_collection,
    get_intake_audit_events_collection,
    get_intake_calls_collection,
)
from app.main import create_app
from app.providers.contracts import StoredObject, TranscriptHandler
from app.security.field_encryption import FieldEncryptor, get_field_encryptor
from app.workflows.consent import (
    ENGLISH_CONSENT_GRANTED_RESPONSE,
    ENGLISH_CONSENT_PROMPT,
    ENGLISH_CONSENT_REPROMPT,
    ENGLISH_CONSENT_UNCLEAR_CLOSURE,
    HINDI_CONSENT_DIALOGUE,
)
from app.workflows.conversation import ConversationReply, ConversationUnavailable

WEBHOOK_TOKEN = "test-webhook-token"
VOICEBOT_USERNAME = "test-voicebot"
VOICEBOT_PASSWORD = "test-voicebot-password"


class FakeCollection:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def update_one(self, filter: dict, update: dict, *, upsert: bool) -> "FakeUpdateResult":
        assert upsert is True
        if any(document["event_key"] == filter["event_key"] for document in self.documents):
            return FakeUpdateResult(upserted_id=None)
        self.documents.append(update["$setOnInsert"])
        return FakeUpdateResult(upserted_id="inserted")


class FakeUpdateResult:
    def __init__(self, upserted_id: str | None, matched_count: int = 0) -> None:
        self.upserted_id = upserted_id
        self.matched_count = matched_count


class FakeIntakeCallsCollection:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def update_one(
        self, filter: dict, update: dict, *, upsert: bool = False
    ) -> FakeUpdateResult:
        document = next(
            (candidate for candidate in self.documents if _matches_filter(candidate, filter)), None
        )
        if document is None:
            if not upsert:
                return FakeUpdateResult(upserted_id=None)
            document = update["$setOnInsert"]
            self.documents.append(document)
            return FakeUpdateResult(upserted_id="inserted")
        for key, value in update.get("$set", {}).items():
            _set_nested_value(document, key, value)
        for key, value in update.get("$push", {}).items():
            _append_nested_value(document, key, value)
        return FakeUpdateResult(upserted_id=None, matched_count=1)

    async def find_one(self, filter: dict, projection: dict | None = None) -> dict | None:
        document = next(
            (candidate for candidate in self.documents if _matches_filter(candidate, filter)), None
        )
        if document is None or projection is None:
            return document
        return {key: document[key] for key in projection if key in document}


class FakeAuditEventsCollection:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> None:
        self.documents.append(document)


def _matches_filter(document: dict, filter: dict) -> bool:
    return all(_nested_value(document, key) == value for key, value in filter.items())


def _nested_value(document: dict, key: str):
    value = document
    for part in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _set_nested_value(document: dict, key: str, value: object) -> None:
    parts = key.split(".")
    target = document
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def _append_nested_value(document: dict, key: str, value: object) -> None:
    parts = key.split(".")
    target = document
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target.setdefault(parts[-1], []).append(value)


class FakeStreamingSpeechToTextSession:
    def __init__(self, on_transcript: TranscriptHandler) -> None:
        self.on_transcript = on_transcript
        self.audio_payloads: list[str] = []
        self.started = False
        self.stopped = False
        self.transcript_on_next_audio: tuple[str, str, str | None] | None = None

    async def start(self) -> None:
        self.started = True

    async def send_audio(self, audio_payload: str) -> None:
        self.audio_payloads.append(audio_payload)
        if self.transcript_on_next_audio:
            event_type, transcript, language = self.transcript_on_next_audio
            self.transcript_on_next_audio = None
            await self.on_transcript(event_type, transcript, language)

    async def stop(self) -> None:
        self.stopped = True


class FakeStreamingTextToSpeechProvider:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.requests: list[tuple[str | None, str | None]] = []

    async def stream(
        self,
        text: str,
        *,
        language_code: str | None = None,
        speaker: str | None = None,
    ):
        from app.providers.contracts import SpeechAudio

        self.texts.append(text)
        self.requests.append((language_code, speaker))
        yield SpeechAudio(content=b"prompt-audio", content_type="audio/L16")


class FakeObjectStorageProvider:
    def __init__(self) -> None:
        self.stored: list[tuple[object, str, bytes, str]] = []

    async def store(self, intake_call_id, filename: str, content: bytes, content_type: str):
        self.stored.append((intake_call_id, filename, content, content_type))
        return StoredObject(
            key=f"intake-calls/{intake_call_id}/{filename}", content_type=content_type
        )

    async def create_download_url(self, key: str) -> str:
        return f"https://storage.example.test/{key}"


class FakeConversationProvider:
    def __init__(self) -> None:
        self.requests = []

    async def reply(self, messages):
        self.requests.append(messages)
        is_hindi = "Speak in Hindi" in messages[0]["content"]
        finished = messages[-1]["content"].lower() in {"nothing", "nothing else"}
        reply = ("धन्यवाद।" if is_hindi else "Thank you.") if finished else (
            "कृपया और बताएं।" if is_hindi else "Please tell me more."
        )
        return ConversationReply(reply=reply, finished=finished)


def make_client(
    *, object_storage_provider: str = "unconfigured"
) -> tuple[
    TestClient,
    FakeCollection,
    list[FakeStreamingSpeechToTextSession],
    list[FakeStreamingTextToSpeechProvider],
]:
    from app.api.routes import telephony

    app = create_app()
    events = FakeCollection()
    intake_calls = FakeIntakeCallsCollection()
    audit_events = FakeAuditEventsCollection()
    events.intake_calls = intake_calls
    events.audit_events = audit_events
    stt_sessions: list[FakeStreamingSpeechToTextSession] = []
    tts_providers: list[FakeStreamingTextToSpeechProvider] = []
    conversation_provider = FakeConversationProvider()
    events.conversation_provider = conversation_provider
    settings = Settings(
        _env_file=None,
        exotel_webhook_token=WEBHOOK_TOKEN,
        public_base_url="https://demo.example.test",
        exotel_voicebot_username=VOICEBOT_USERNAME,
        exotel_voicebot_password=VOICEBOT_PASSWORD,
        sarvam_api_key="test-sarvam-api-key",
        object_storage_provider=object_storage_provider,
        gcs_bucket_name="demo-private-bucket" if object_storage_provider == "gcs" else "",
    )

    def create_fake_stt_session(
        settings: Settings, on_transcript: TranscriptHandler
    ) -> FakeStreamingSpeechToTextSession:
        assert settings.sarvam_api_key == "test-sarvam-api-key"
        session = FakeStreamingSpeechToTextSession(on_transcript)
        stt_sessions.append(session)
        return session

    def create_fake_tts_provider(settings: Settings) -> FakeStreamingTextToSpeechProvider:
        assert settings.sarvam_api_key == "test-sarvam-api-key"
        provider = FakeStreamingTextToSpeechProvider()
        tts_providers.append(provider)
        return provider

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_call_events_collection] = lambda: events
    app.dependency_overrides[get_intake_calls_collection] = lambda: intake_calls
    app.dependency_overrides[get_intake_audit_events_collection] = lambda: audit_events
    encryptor = FieldEncryptor(Fernet.generate_key().decode())
    events.encryptor = encryptor
    app.dependency_overrides[get_field_encryptor] = lambda: encryptor
    telephony._create_stt_session = create_fake_stt_session
    telephony._create_tts_provider = create_fake_tts_provider
    telephony._create_conversation_provider = lambda settings: conversation_provider
    return TestClient(app), events, stt_sessions, tts_providers


def resolve_voicebot_stream_path(client: TestClient, call_sid: str) -> str:
    response = client.get(
        "/api/v1/telephony/exotel/voicebot?"
        f"CallSid={call_sid}&CallFrom=%2B919999999999&token={WEBHOOK_TOKEN}"
    )
    assert response.status_code == 200
    url = urlsplit(response.json()["url"])
    return f"{url.path}?{url.query}"


def test_exotel_passthru_records_event() -> None:
    client, events, _, _ = make_client()
    with client:
        response = client.get(
            "/api/v1/telephony/exotel/passthru?callsid=demo-call-001&direction=inbound"
            f"&token={WEBHOOK_TOKEN}"
        )

    assert response.status_code == 200
    assert response.json() == {
        "status": "received",
        "provider": "exotel",
        "call_sid": "demo-call-001",
    }
    assert len(events.documents) == 1
    event = events.documents[0]
    assert event["provider"] == "exotel"
    assert event["provider_call_id"] == "demo-call-001"
    assert event["event_type"] == "passthru"
    assert event["direction"] == "inbound"
    assert "received_at" in event


def test_exotel_passthru_accepts_exotel_parameter_casing() -> None:
    client, events, _, _ = make_client()
    with client:
        response = client.get(
            "/api/v1/telephony/exotel/passthru?CallSid=exotel-call-001&Direction=incoming"
            f"&token={WEBHOOK_TOKEN}"
        )

    assert response.status_code == 200
    assert events.documents[0]["provider_call_id"] == "exotel-call-001"
    assert events.documents[0]["direction"] == "incoming"


def test_exotel_passthru_ignores_duplicate_callbacks() -> None:
    client, events, _, _ = make_client()
    url = (
        "/api/v1/telephony/exotel/passthru?CallSid=exotel-call-duplicate&Direction=incoming"
        f"&token={WEBHOOK_TOKEN}"
    )
    with client:
        first = client.get(url)
        second = client.get(url)

    assert first.json()["status"] == "received"
    assert second.json()["status"] == "duplicate"
    assert len(events.documents) == 1


def test_exotel_passthru_requires_call_sid() -> None:
    client, events, _, _ = make_client()
    with client:
        response = client.get(f"/api/v1/telephony/exotel/passthru?token={WEBHOOK_TOKEN}")

    assert response.status_code == 422
    assert response.json() == {"detail": "callsid required"}
    assert events.documents == []


def test_exotel_status_records_form_body_without_phone_numbers() -> None:
    client, events, _, _ = make_client()
    with client:
        response = client.post(
            f"/api/v1/telephony/exotel/status?token={WEBHOOK_TOKEN}",
            content="CallSid=sid-1&Status=completed&Duration=42&From=%2B910000000000"
            "&RecordingUrl=https%3A%2F%2Fexample.com%2Fr.mp3",
            headers={"content-type": "application/x-www-form-urlencoded"},
        )

    assert response.status_code == 200
    event = events.documents[0]
    assert event["event_type"] == "status"
    assert event["provider_call_id"] == "sid-1"
    assert event["call_status"] == "completed"
    assert event["duration_seconds"] == "42"
    assert event["recording_available"] is True
    assert "+910000000000" not in str(event)
    assert "https://example.com/r.mp3" not in str(event)


def test_exotel_status_accepts_query_string_and_requires_call_sid() -> None:
    client, events, _, _ = make_client()
    with client:
        ok = client.get(
            f"/api/v1/telephony/exotel/status?CallSid=sid-2&Status=busy&RecordingUrl=null"
            f"&token={WEBHOOK_TOKEN}"
        )
        missing = client.post(
            f"/api/v1/telephony/exotel/status?token={WEBHOOK_TOKEN}",
            content="Status=busy",
        )

    assert ok.status_code == 200
    assert events.documents[0]["call_status"] == "busy"
    assert events.documents[0]["recording_available"] is False
    assert missing.status_code == 422
    assert len(events.documents) == 1


def test_exotel_webhooks_reject_an_invalid_token() -> None:
    client, events, _, _ = make_client()
    with client:
        response = client.get("/api/v1/telephony/exotel/passthru?CallSid=sid-3&token=invalid")

    assert response.status_code == 401
    assert events.documents == []


def test_exotel_voicebot_resolver_returns_websocket_url() -> None:
    client, events, _, _ = make_client()
    with client:
        response = client.get(
            "/api/v1/telephony/exotel/voicebot?CallSid=sid-voicebot"
            f"&CallFrom=%2B919999999999&token={WEBHOOK_TOKEN}"
        )

    assert response.status_code == 200
    url = urlsplit(response.json()["url"])
    assert url.scheme == "wss"
    assert url.username is not None
    assert url.username.startswith("test-voicebot.")
    assert url.password == "test-voicebot-password"
    assert url.hostname == "demo.example.test"
    assert url.path == "/api/v1/telephony/exotel/voicebot/stream"
    assert "session_token=" in url.query
    assert events.intake_calls.documents[0]["status"] == "consent_pending"
    assert "+919999999999" not in str(events.intake_calls.documents)
    assert [event["event_type"] for event in events.audit_events.documents] == ["intake_created"]


def test_exotel_voicebot_stream_requires_basic_authentication() -> None:
    client, _, _, _ = make_client()
    with client, pytest.raises(WebSocketDisconnect) as error:
        stream_path = resolve_voicebot_stream_path(client, "sid-unauthenticated")
        with client.websocket_connect(stream_path):
            pass

    assert error.value.code == 1008


def test_exotel_voicebot_stream_resolves_database_dependency_for_websocket() -> None:
    client, _, _, _ = make_client()
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()

    with client:
        stream_path = resolve_voicebot_stream_path(client, "sid-dependency")
        with client.websocket_connect(
            stream_path,
            headers={"Authorization": f"Basic {credentials}"},
        ):
            pass


def test_exotel_voicebot_stream_relays_media_to_sarvam_stt_and_plays_language_prompt() -> None:
    client, events, stt_sessions, tts_providers = make_client()
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()
    with client:
        stream_path = resolve_voicebot_stream_path(client, "sid-stream-test")
        with client.websocket_connect(
            stream_path,
            headers={"Authorization": f"Basic {credentials}"},
        ) as websocket:
            websocket.send_json({"event": "start", "start": {"stream_sid": "stream-test-001"}})
            prompt_audio = websocket.receive_json()
            websocket.receive_json()
            websocket.send_json({"event": "media", "media": {"payload": "cGNtLWF1ZGlv"}})
            websocket.send_json({"event": "stop"})

    assert len(stt_sessions) == 1
    assert stt_sessions[0].started is True
    assert stt_sessions[0].audio_payloads == ["cGNtLWF1ZGlv"]
    assert stt_sessions[0].stopped is True
    assert tts_providers[0].texts == [LANGUAGE_SELECTION_PROMPT]
    assert tts_providers[1].texts == [HINDI_LANGUAGE_SELECTION_PROMPT]
    assert prompt_audio == {
        "event": "media",
        "stream_sid": "stream-test-001",
        "media": {"payload": "cHJvbXB0LWF1ZGlv"},
    }
    assert [event["event_type"] for event in events.documents] == [
        "voicebot_stream_started",
        "voicebot_stream_stopped",
    ]
    assert "cGNtLWF1ZGlv" not in str(events.documents)


def test_exotel_voicebot_stream_routes_final_consent_to_tts_response() -> None:
    client, events, stt_sessions, tts_providers = make_client()
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()
    with client:
        stream_path = resolve_voicebot_stream_path(client, "sid-consent-yes")
        with client.websocket_connect(
            stream_path,
            headers={"Authorization": f"Basic {credentials}"},
        ) as websocket:
            websocket.send_json({"event": "start", "start": {"stream_sid": "stream-consent-yes"}})
            websocket.receive_json()
            websocket.receive_json()
            stt_sessions[0].transcript_on_next_audio = (
                "transcript.final",
                "English",
                "en-IN",
            )
            websocket.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
            websocket.receive_json()
            stt_sessions[0].transcript_on_next_audio = (
                "transcript.final",
                "Yes, I consent.",
                "en-IN",
            )
            websocket.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
            response_audio = websocket.receive_json()
            websocket.receive_json()
            websocket.send_json({"event": "stop"})

    assert tts_providers[0].texts == [LANGUAGE_SELECTION_PROMPT]
    assert tts_providers[2].texts == [ENGLISH_CONSENT_PROMPT]
    assert tts_providers[3].texts == [ENGLISH_CONSENT_GRANTED_RESPONSE]
    assert tts_providers[4].texts == ["Please tell me more."]
    assert response_audio == {
        "event": "media",
        "stream_sid": "stream-consent-yes",
        "media": {"payload": "cHJvbXB0LWF1ZGlv"},
    }
    assert [event["event_type"] for event in events.documents] == [
        "voicebot_stream_started",
        "voicebot_language_selected",
        "voicebot_consent_granted",
        "voicebot_stream_stopped",
    ]
    intake_call = events.intake_calls.documents[0]
    assert intake_call["status"] == "in_progress"
    assert intake_call["language"]["preferred"] == "english"
    assert intake_call["disclosures"]["recording_consent"] is True
    assert intake_call["disclosures"]["intake_consent"] is True
    assert "voicebot_session_token_hash" in intake_call
    assert [event["event_type"] for event in events.audit_events.documents] == [
        "intake_created",
        "consent_recorded",
    ]


def test_exotel_voicebot_stream_stores_only_post_consent_audio_in_gcs(monkeypatch) -> None:
    from app.api.routes import telephony

    storage_provider = FakeObjectStorageProvider()
    monkeypatch.setattr(
        telephony, "_create_object_storage_provider", lambda settings: storage_provider
    )
    client, events, stt_sessions, _ = make_client(object_storage_provider="gcs")
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()

    with client:
        stream_path = resolve_voicebot_stream_path(client, "sid-recording")
        with client.websocket_connect(
            stream_path,
            headers={"Authorization": f"Basic {credentials}"},
        ) as websocket:
            websocket.send_json({"event": "start", "start": {"stream_sid": "stream-recording"}})
            websocket.receive_json()
            websocket.receive_json()
            stt_sessions[0].transcript_on_next_audio = ("transcript.final", "English", "en-IN")
            websocket.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
            websocket.receive_json()
            stt_sessions[0].transcript_on_next_audio = (
                "transcript.final",
                "Yes, I consent.",
                "en-IN",
            )
            websocket.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
            websocket.receive_json()
            websocket.receive_json()
            websocket.send_json(
                {"event": "media", "media": {"payload": "Y2FsbGVyLXBvc3QtY29uc2VudCE="}}
            )
            websocket.send_json({"event": "stop"})

    assert [item[1] for item in storage_provider.stored] == [
        "recording-caller.wav",
        "recording-agent.wav",
    ]
    caller_wav = storage_provider.stored[0][2]
    with wave_open(BytesIO(caller_wav), "rb") as wav_file:
        assert wav_file.readframes(wav_file.getnframes()) == b"caller-post-consent!"
    intake_call = events.intake_calls.documents[0]
    artifacts = intake_call["intake"]["artifacts"]
    assert [artifact["storage_key"] for artifact in artifacts] == [
        f"intake-calls/{intake_call['id']}/recording-caller.wav",
        f"intake-calls/{intake_call['id']}/recording-agent.wav",
    ]
    assert intake_call["disclosures"]["recording_started_at"] is not None
    assert intake_call["disclosures"]["recording_stopped_at"] is not None
    assert [event["event_type"] for event in events.audit_events.documents] == [
        "intake_created",
        "consent_recorded",
        "artifact_attached",
        "artifact_attached",
    ]


def test_exotel_voicebot_stream_reprompts_then_stops_processing_unclear_consent() -> None:
    client, events, stt_sessions, tts_providers = make_client()
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()
    with client:
        stream_path = resolve_voicebot_stream_path(client, "sid-consent-unclear")
        with client.websocket_connect(
            stream_path,
            headers={"Authorization": f"Basic {credentials}"},
        ) as websocket:
            websocket.send_json(
                {"event": "start", "start": {"stream_sid": "stream-consent-unclear"}}
            )
            websocket.receive_json()
            websocket.receive_json()
            stt_sessions[0].transcript_on_next_audio = (
                "transcript.final",
                "English",
                "en-IN",
            )
            websocket.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
            websocket.receive_json()
            stt_sessions[0].transcript_on_next_audio = (
                "transcript.final",
                "What is this about?",
                "en-IN",
            )
            websocket.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
            websocket.receive_json()
            stt_sessions[0].transcript_on_next_audio = (
                "transcript.final",
                "I am not sure.",
                "en-IN",
            )
            websocket.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
            closing_audio = websocket.receive_json()

    assert tts_providers[3].texts == [ENGLISH_CONSENT_REPROMPT]
    assert tts_providers[4].texts == [ENGLISH_CONSENT_UNCLEAR_CLOSURE]
    assert closing_audio["event"] == "media"
    assert [event["event_type"] for event in events.documents[:4]] == [
        "voicebot_stream_started",
        "voicebot_language_selected",
        "voicebot_reprompt_consent",
        "voicebot_close_call",
    ]


def test_exotel_voicebot_stream_uses_hindi_consent_dialogue() -> None:
    client, events, stt_sessions, tts_providers = make_client()
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()
    with client:
        stream_path = resolve_voicebot_stream_path(client, "sid-hindi-consent")
        with client.websocket_connect(
            stream_path,
            headers={"Authorization": f"Basic {credentials}"},
        ) as websocket:
            websocket.send_json({"event": "start", "start": {"stream_sid": "stream-hindi-consent"}})
            websocket.receive_json()
            websocket.receive_json()
            stt_sessions[0].transcript_on_next_audio = (
                "transcript.final",
                "हिंदी",
                "hi-IN",
            )
            websocket.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
            websocket.receive_json()
            websocket.send_json({"event": "stop"})

    assert tts_providers[2].texts == [HINDI_CONSENT_DIALOGUE.prompt]
    assert tts_providers[2].requests == [("hi-IN", "aparna_hi_customer")]
    language_event = events.documents[1]
    assert language_event["event_type"] == "voicebot_language_selected"
    assert language_event["language"] == "hindi"


@pytest.mark.parametrize(
    ("chosen", "expected_code", "expected_speaker"),
    [("English", "en-IN", "simran_en_customer"), ("हिंदी", "hi-IN", "aparna_hi_customer")],
)
def test_post_consent_mixed_language_details_are_encrypted_and_stored_once(
    chosen, expected_code, expected_speaker
) -> None:
    client, events, stt_sessions, tts_providers = make_client()
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()
    turns = [
        ("Mera naam नमिता hai", "hi-IN"),
        ("दिल्ली, near Saket", "hi-IN"),
        ("राहुल", "hi-IN"),
        ("No urgent deadlines", "en-IN"),
        ("तलाक", "hi-IN"),
        ("15 March 2024 ko separation hua", "hi-IN"),
        ("Marriage 2018 mein hui", "hi-IN"),
        ("We live separately", "en-IN"),
        ("Mujhe process samajhna hai", "hi-IN"),
        ("No cases", "en-IN"),
        ("Nothing else", "en-IN"),
    ]
    with client:
        path = resolve_voicebot_stream_path(client, "mixed-language")
        with client.websocket_connect(
            path, headers={"Authorization": f"Basic {credentials}"}
        ) as ws:
            ws.send_json({"event": "start", "start": {"stream_sid": "mixed-stream"}})
            ws.receive_json()
            ws.receive_json()
            for index, (utterance, detected) in enumerate(
                [(chosen, "en-IN"), ("haan", "hi-IN"), *turns]
            ):
                stt_sessions[0].transcript_on_next_audio = ("transcript.final", utterance, detected)
                ws.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
                ws.receive_json()
                if index == 1:
                    ws.receive_json()
            ws.send_json({"event": "stop"})

    intake = events.intake_calls.documents[0]["intake"]
    assert events.intake_calls.documents[0]["status"] == "completed"
    segments = [s for s in intake["transcript_segments"] if s["speaker"] == "caller"]
    agent_segments = [s for s in intake["transcript_segments"] if s["speaker"] == "agent"]
    assert [events.encryptor.decrypt(s["text_encrypted"]) for s in segments] == [
        text for text, _ in turns
    ]
    assert [s["detected_language"] for s in segments] == [language for _, language in turns]
    assert "Mera naam" not in str(intake)
    assert intake["facts"] == []
    assert len(agent_segments) == len(turns) + 2
    assert all(s["detected_language"] == expected_code for s in agent_segments)
    assert all(s["text_encrypted"].startswith("fernet:v1:") for s in agent_segments)
    assert all(p.requests == [(expected_code, expected_speaker)] for p in tts_providers[2:])


def test_declined_consent_does_not_collect_post_consent_details() -> None:
    client, events, stt_sessions, _ = make_client()
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()
    with client:
        path = resolve_voicebot_stream_path(client, "declined-details")
        with client.websocket_connect(
            path, headers={"Authorization": f"Basic {credentials}"}
        ) as ws:
            ws.send_json({"event": "start", "start": {"stream_sid": "declined-stream"}})
            ws.receive_json()
            ws.receive_json()
            for utterance in ["English", "no"]:
                stt_sessions[0].transcript_on_next_audio = ("transcript.final", utterance, "en-IN")
                ws.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
                ws.receive_json()
            ws.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
            ws.send_json({"event": "stop"})
    assert len(stt_sessions[0].audio_payloads) == 2
    assert events.intake_calls.documents[0]["intake"]["transcript_segments"] == []
    assert events.intake_calls.documents[0]["intake"]["facts"] == []
    assert events.conversation_provider.requests == []


def test_general_intake_preserves_answers_without_inventing_facts() -> None:
    client, events, stt_sessions, _ = make_client()
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()
    turns = ["skip", "Delhi", "pata nahi", "no", "custody", "2024", "no", "nothing"]
    with client:
        path = resolve_voicebot_stream_path(client, "general-intake")
        with client.websocket_connect(
            path, headers={"Authorization": f"Basic {credentials}"}
        ) as ws:
            ws.send_json({"event": "start", "start": {"stream_sid": "general-stream"}})
            ws.receive_json()
            ws.receive_json()
            for index, utterance in enumerate(["हिंदी", "yes", *turns]):
                stt_sessions[0].transcript_on_next_audio = ("transcript.final", utterance, "hi-IN")
                ws.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
                ws.receive_json()
                if index == 1:
                    ws.receive_json()
            ws.send_json({"event": "stop"})
    call = events.intake_calls.documents[0]
    assert call["status"] == "completed"
    assert call["intake"]["matter_type"] is None
    assert call["intake"]["facts"] == []
    segments = [s for s in call["intake"]["transcript_segments"] if s["speaker"] == "caller"]
    assert [events.encryptor.decrypt(s["text_encrypted"]) for s in segments] == turns


def test_conversation_provider_failure_stops_intake_and_keeps_previous_transcript() -> None:
    client, events, stt_sessions, _ = make_client()
    provider = events.conversation_provider
    original_reply = provider.reply

    async def fail_after_first_reply(messages):
        if provider.requests:
            raise ConversationUnavailable("unavailable")
        return await original_reply(messages)

    provider.reply = fail_after_first_reply
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()
    with client:
        path = resolve_voicebot_stream_path(client, "model-failure")
        with client.websocket_connect(
            path, headers={"Authorization": f"Basic {credentials}"}
        ) as ws:
            ws.send_json({"event": "start", "start": {"stream_sid": "model-failure-stream"}})
            ws.receive_json()
            ws.receive_json()
            for index, utterance in enumerate(["English", "yes", "A private response"]):
                stt_sessions[0].transcript_on_next_audio = ("transcript.final", utterance, "en-IN")
                ws.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
                ws.receive_json()
                if index == 1:
                    ws.receive_json()
            ws.send_json({"event": "stop"})
    call = events.intake_calls.documents[0]
    assert call["status"] == "abandoned"
    caller_segments = [s for s in call["intake"]["transcript_segments"] if s["speaker"] == "caller"]
    assert events.encryptor.decrypt(caller_segments[0]["text_encrypted"]) == "A private response"
    assert any(e["event_type"] == "voicebot_conversation_unavailable" for e in events.documents)


def _stream(client, sid: str, stream_sid: str):
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()
    return client.websocket_connect(
        resolve_voicebot_stream_path(client, sid),
        headers={"Authorization": f"Basic {credentials}"},
    )


def test_duplicate_start_event_is_ignored() -> None:
    client, events, stt_sessions, tts_providers = make_client()
    with client:
        with _stream(client, "dup-start", "s1") as ws:
            ws.send_json({"event": "start", "start": {"stream_sid": "s1"}})
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"event": "start", "start": {"stream_sid": "s2"}})
            ws.send_json({"event": "stop"})
    assert len(stt_sessions) == 1 and len(tts_providers) == 2


def test_outbound_audio_frames_are_sample_aligned(monkeypatch) -> None:
    from app.api.routes import telephony
    from app.providers.contracts import SpeechAudio

    class OddChunkTts:
        async def stream(self, text, *, language_code=None, speaker=None):
            yield SpeechAudio(content=b"abc", content_type="audio/L16")
            yield SpeechAudio(content=b"defg", content_type="audio/L16")

    client, *_ = make_client()
    monkeypatch.setattr(telephony, "_create_tts_provider", lambda settings: OddChunkTts())
    with client:
        with _stream(client, "aligned", "s1") as ws:
            ws.send_json({"event": "start", "start": {"stream_sid": "s1"}})
            frames = [b64decode(ws.receive_json()["media"]["payload"]) for _ in range(2)]
            ws.send_json({"event": "stop"})
    assert frames == [b"ab", b"cdef"]


def test_unexpected_turn_failure_ends_the_call_instead_of_silently_stalling(monkeypatch) -> None:
    from app.api.routes import telephony

    client, events, stt_sessions, tts_providers = make_client()
    prompts_spoken = 0
    original = telephony._create_tts_provider

    def failing_after_prompts(settings):
        nonlocal prompts_spoken
        prompts_spoken += 1
        if prompts_spoken > 2:
            raise RuntimeError("tts exploded")
        return original(settings)

    monkeypatch.setattr(telephony, "_create_tts_provider", failing_after_prompts)
    with client:
        with _stream(client, "turn-failure", "s1") as ws:
            ws.send_json({"event": "start", "start": {"stream_sid": "s1"}})
            ws.receive_json()
            ws.receive_json()
            stt_sessions[0].transcript_on_next_audio = ("transcript.final", "English", "en-IN")
            ws.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
    assert any(e["event_type"] == "voicebot_turn_failed" for e in events.documents)


def test_settings_reject_mismatched_stt_and_tts_sample_rates() -> None:
    with pytest.raises(ValueError, match="must match"):
        Settings(_env_file=None, sarvam_stt_sample_rate=8000, sarvam_tts_sample_rate=24000)


def _slow_tts_with_barge_in(monkeypatch, *, barge_in: bool):
    import asyncio

    from app.api.routes import telephony
    from app.providers.contracts import SpeechAudio

    streamed = []

    class SlowTts:
        async def stream(self, text, *, language_code=None, speaker=None):
            streamed.append(text)
            yield SpeechAudio(content=b"ab", content_type="audio/L16")
            await asyncio.sleep(30)  # still "speaking" until interrupted
            yield SpeechAudio(content=b"cd", content_type="audio/L16")

    client, events, stt_sessions, _ = make_client()
    settings = client.app.dependency_overrides[get_settings]()
    client.app.dependency_overrides[get_settings] = lambda: settings.model_copy(
        update={"voice_barge_in_enabled": barge_in}
    )
    monkeypatch.setattr(telephony, "_create_tts_provider", lambda settings: SlowTts())
    return client, events, stt_sessions, streamed


def test_barge_in_stops_the_agent_and_clears_buffered_audio(monkeypatch) -> None:
    client, events, stt_sessions, streamed = _slow_tts_with_barge_in(monkeypatch, barge_in=True)
    with client:
        with _stream(client, "barge-in", "s1") as ws:
            ws.send_json({"event": "start", "start": {"stream_sid": "s1"}})
            assert ws.receive_json()["event"] == "media"
            stt_sessions[0].transcript_on_next_audio = ("transcript.partial", "Eng", "en-IN")
            ws.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
            assert ws.receive_json() == {"event": "clear", "stream_sid": "s1"}
            ws.send_json({"event": "stop"})
            assert ws.receive()["type"] == "websocket.close"
    # The Hindi half of the opening prompt is dropped along with the interrupted English half.
    assert len(streamed) == 1
    assert any(e["event_type"] == "voicebot_agent_interrupted" for e in events.documents)


def test_partial_transcripts_do_not_interrupt_when_barge_in_is_off(monkeypatch) -> None:
    client, events, stt_sessions, streamed = _slow_tts_with_barge_in(monkeypatch, barge_in=False)
    with client:
        with _stream(client, "no-barge-in", "s1") as ws:
            ws.send_json({"event": "start", "start": {"stream_sid": "s1"}})
            ws.receive_json()
            stt_sessions[0].transcript_on_next_audio = ("transcript.partial", "Eng", "en-IN")
            ws.send_json({"event": "media", "media": {"payload": "YXVkaW8="}})
            ws.send_json({"event": "stop"})
            assert ws.receive()["type"] == "websocket.close"
    assert not any(e["event_type"] == "voicebot_agent_interrupted" for e in events.documents)
