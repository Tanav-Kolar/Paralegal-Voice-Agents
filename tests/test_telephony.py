from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.db.mongodb import get_call_events_collection
from app.main import create_app

WEBHOOK_TOKEN = "test-webhook-token"


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


def make_client() -> tuple[TestClient, FakeCollection]:
    app = create_app()
    events = FakeCollection()
    settings = Settings(_env_file=None, exotel_webhook_token=WEBHOOK_TOKEN)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_call_events_collection] = lambda: events
    return TestClient(app), events


def test_exotel_passthru_records_event() -> None:
    client, events = make_client()
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
    client, events = make_client()
    with client:
        response = client.get(
            "/api/v1/telephony/exotel/passthru?CallSid=exotel-call-001&Direction=incoming"
            f"&token={WEBHOOK_TOKEN}"
        )

    assert response.status_code == 200
    assert events.documents[0]["provider_call_id"] == "exotel-call-001"
    assert events.documents[0]["direction"] == "incoming"


def test_exotel_passthru_ignores_duplicate_callbacks() -> None:
    client, events = make_client()
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
    client, events = make_client()
    with client:
        response = client.get(f"/api/v1/telephony/exotel/passthru?token={WEBHOOK_TOKEN}")

    assert response.status_code == 422
    assert response.json() == {"detail": "callsid required"}
    assert events.documents == []


def test_exotel_status_records_form_body_without_phone_numbers() -> None:
    client, events = make_client()
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
    client, events = make_client()
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
    client, events = make_client()
    with client:
        response = client.get("/api/v1/telephony/exotel/passthru?CallSid=sid-3&token=invalid")

    assert response.status_code == 401
    assert events.documents == []
