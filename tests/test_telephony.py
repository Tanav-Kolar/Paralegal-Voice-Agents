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
