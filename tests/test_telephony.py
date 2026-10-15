from fastapi.testclient import TestClient

from app.db.mongodb import get_call_events_collection
from app.main import create_app


class FakeCollection:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> None:
        self.documents.append(document)


def make_client() -> tuple[TestClient, FakeCollection]:
    app = create_app()
    events = FakeCollection()
    app.dependency_overrides[get_call_events_collection] = lambda: events
    return TestClient(app), events


def test_exotel_passthru_records_event() -> None:
    client, events = make_client()
    with client:
        response = client.get(
            "/api/v1/telephony/exotel/passthru?callsid=demo-call-001&direction=inbound"
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
        )

    assert response.status_code == 200
    assert events.documents[0]["provider_call_id"] == "exotel-call-001"
    assert events.documents[0]["direction"] == "incoming"


def test_exotel_passthru_requires_call_sid() -> None:
    client, events = make_client()
    with client:
        response = client.get("/api/v1/telephony/exotel/passthru")

    assert response.status_code == 422
    assert response.json() == {"detail": "callsid required"}
    assert events.documents == []


def test_exotel_status_records_form_body_without_phone_numbers() -> None:
    client, events = make_client()
    with client:
        response = client.post(
            "/api/v1/telephony/exotel/status",
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
    assert event["recording_url"] == "https://example.com/r.mp3"
    assert "+910000000000" not in str(event)


def test_exotel_status_accepts_query_string_and_requires_call_sid() -> None:
    client, events = make_client()
    with client:
        ok = client.get("/api/v1/telephony/exotel/status?CallSid=sid-2&Status=busy")
        missing = client.post("/api/v1/telephony/exotel/status", content="Status=busy")

    assert ok.status_code == 200
    assert events.documents[0]["call_status"] == "busy"
    assert missing.status_code == 422
    assert len(events.documents) == 1
