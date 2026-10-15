from base64 import b64encode

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.config import Settings, get_settings
from app.db.mongodb import get_call_events_collection
from app.main import create_app
from app.providers.contracts import TranscriptHandler

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
    def __init__(self, upserted_id: str | None) -> None:
        self.upserted_id = upserted_id


class FakeStreamingSpeechToTextSession:
    def __init__(self, on_transcript: TranscriptHandler) -> None:
        self.on_transcript = on_transcript
        self.audio_payloads: list[str] = []
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def send_audio(self, audio_payload: str) -> None:
        self.audio_payloads.append(audio_payload)

    async def stop(self) -> None:
        self.stopped = True


def make_client() -> tuple[TestClient, FakeCollection, list[FakeStreamingSpeechToTextSession]]:
    from app.api.routes import telephony

    app = create_app()
    events = FakeCollection()
    stt_sessions: list[FakeStreamingSpeechToTextSession] = []
    settings = Settings(
        _env_file=None,
        exotel_webhook_token=WEBHOOK_TOKEN,
        public_base_url="https://demo.example.test",
        exotel_voicebot_username=VOICEBOT_USERNAME,
        exotel_voicebot_password=VOICEBOT_PASSWORD,
        sarvam_api_key="test-sarvam-api-key",
    )

    def create_fake_stt_session(
        settings: Settings, on_transcript: TranscriptHandler
    ) -> FakeStreamingSpeechToTextSession:
        assert settings.sarvam_api_key == "test-sarvam-api-key"
        session = FakeStreamingSpeechToTextSession(on_transcript)
        stt_sessions.append(session)
        return session

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_call_events_collection] = lambda: events
    telephony._create_stt_session = create_fake_stt_session
    return TestClient(app), events, stt_sessions


def test_exotel_passthru_records_event() -> None:
    client, events, _ = make_client()
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
    client, events, _ = make_client()
    with client:
        response = client.get(
            "/api/v1/telephony/exotel/passthru?CallSid=exotel-call-001&Direction=incoming"
            f"&token={WEBHOOK_TOKEN}"
        )

    assert response.status_code == 200
    assert events.documents[0]["provider_call_id"] == "exotel-call-001"
    assert events.documents[0]["direction"] == "incoming"


def test_exotel_passthru_ignores_duplicate_callbacks() -> None:
    client, events, _ = make_client()
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
    client, events, _ = make_client()
    with client:
        response = client.get(f"/api/v1/telephony/exotel/passthru?token={WEBHOOK_TOKEN}")

    assert response.status_code == 422
    assert response.json() == {"detail": "callsid required"}
    assert events.documents == []


def test_exotel_status_records_form_body_without_phone_numbers() -> None:
    client, events, _ = make_client()
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
    client, events, _ = make_client()
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
    client, events, _ = make_client()
    with client:
        response = client.get("/api/v1/telephony/exotel/passthru?CallSid=sid-3&token=invalid")

    assert response.status_code == 401
    assert events.documents == []


def test_exotel_voicebot_resolver_returns_websocket_url() -> None:
    client, _, _ = make_client()
    with client:
        response = client.get(
            f"/api/v1/telephony/exotel/voicebot?CallSid=sid-voicebot&token={WEBHOOK_TOKEN}"
        )

    assert response.status_code == 200
    assert response.json() == {
        "url": "wss://test-voicebot:test-voicebot-password@demo.example.test/"
        "api/v1/telephony/exotel/voicebot/stream"
    }


def test_exotel_voicebot_stream_requires_basic_authentication() -> None:
    client, _, _ = make_client()
    with client, pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/api/v1/telephony/exotel/voicebot/stream"):
            pass

    assert error.value.code == 1008


def test_exotel_voicebot_stream_resolves_database_dependency_for_websocket() -> None:
    app = create_app()
    settings = Settings(
        _env_file=None,
        exotel_voicebot_username=VOICEBOT_USERNAME,
        exotel_voicebot_password=VOICEBOT_PASSWORD,
    )
    app.dependency_overrides[get_settings] = lambda: settings
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()

    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/telephony/exotel/voicebot/stream",
            headers={"Authorization": f"Basic {credentials}"},
        ):
            pass


def test_exotel_voicebot_stream_relays_media_to_sarvam_stt() -> None:
    client, events, stt_sessions = make_client()
    credentials = b64encode(f"{VOICEBOT_USERNAME}:{VOICEBOT_PASSWORD}".encode()).decode()
    with client:
        with client.websocket_connect(
            "/api/v1/telephony/exotel/voicebot/stream",
            headers={"Authorization": f"Basic {credentials}"},
        ) as websocket:
            websocket.send_json(
                {"event": "start", "start": {"stream_sid": "stream-test-001"}}
            )
            websocket.send_json({"event": "media", "media": {"payload": "cGNtLWF1ZGlv"}})
            websocket.send_json({"event": "stop"})

    assert len(stt_sessions) == 1
    assert stt_sessions[0].started is True
    assert stt_sessions[0].audio_payloads == ["cGNtLWF1ZGlv"]
    assert stt_sessions[0].stopped is True
    assert [event["event_type"] for event in events.documents] == [
        "voicebot_stream_started",
        "voicebot_stream_stopped",
    ]
    assert "cGNtLWF1ZGlv" not in str(events.documents)
