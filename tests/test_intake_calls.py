from fastapi.testclient import TestClient

from app.db.mongodb import get_intake_calls_collection
from app.main import create_app


class FakeIntakeCallsCollection:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> None:
        self.documents.append(document)


def test_create_intake_call_keeps_artifact_as_private_reference() -> None:
    app = create_app()
    collection = FakeIntakeCallsCollection()
    app.dependency_overrides[get_intake_calls_collection] = lambda: collection
    payload = {
        "telephony": {
            "provider": "exotel",
            "provider_call_id": "call-001",
            "incoming_number": "+919999999999",
            "caller_phone_encrypted": "encrypted-caller-phone",
            "caller_phone_masked": "+91******9999",
            "started_at": "2026-10-15T10:00:00Z",
        },
        "disclosures": {
            "intake_consent": True,
            "intake_consent_at": "2026-10-15T10:00:15Z",
        },
        "intake": {
            "matter_type": "divorce_separation",
            "artifacts": [
                {
                    "kind": "call_recording",
                    "storage_provider": "gcs",
                    "storage_key": "intake-calls/call-001/recording.wav",
                    "content_type": "audio/wav",
                }
            ],
        },
    }

    with TestClient(app) as client:
        response = client.post("/api/v1/intake-calls", json=payload)

    assert response.status_code == 201
    document = collection.documents[0]
    artifact = document["intake"]["artifacts"][0]
    assert artifact["storage_provider"] == "gcs"
    assert artifact["storage_key"] == "intake-calls/call-001/recording.wav"
    assert "url" not in artifact
    assert response.json()["lawyer_review"]["status"] == "pending"


def test_create_intake_call_rejects_non_private_storage_reference() -> None:
    app = create_app()
    collection = FakeIntakeCallsCollection()
    app.dependency_overrides[get_intake_calls_collection] = lambda: collection
    payload = {
        "telephony": {
            "provider": "exotel",
            "provider_call_id": "call-002",
            "incoming_number": "+919999999999",
            "caller_phone_encrypted": "encrypted-caller-phone",
            "caller_phone_masked": "+91******9999",
            "started_at": "2026-10-15T10:00:00Z",
        },
        "intake": {
            "artifacts": [
                {
                    "kind": "call_recording",
                    "storage_provider": "gcs",
                    "storage_key": "/public/recording.wav",
                    "content_type": "audio/wav",
                }
            ]
        },
    }

    with TestClient(app) as client:
        response = client.post("/api/v1/intake-calls", json=payload)

    assert response.status_code == 422
