from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.db.mongodb import get_intake_audit_events_collection, get_intake_calls_collection
from app.main import create_app
from app.security.field_encryption import FieldEncryptor, get_field_encryptor


class FakeIntakeCallsCollection:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> None:
        self.documents.append(document)


class FakeAuditEventsCollection:
    def __init__(self) -> None:
        self.documents: list[dict] = []

    async def insert_one(self, document: dict) -> None:
        self.documents.append(document)


def test_create_intake_call_keeps_artifact_as_private_reference() -> None:
    app = create_app()
    collection = FakeIntakeCallsCollection()
    audit_collection = FakeAuditEventsCollection()
    app.dependency_overrides[get_intake_calls_collection] = lambda: collection
    app.dependency_overrides[get_intake_audit_events_collection] = lambda: audit_collection
    encryptor = FieldEncryptor(Fernet.generate_key().decode())
    app.dependency_overrides[get_field_encryptor] = lambda: encryptor
    payload = {
        "telephony": {
            "provider": "exotel",
            "provider_call_id": "call-001",
            "caller_phone": "+919999999999",
            "started_at": "2026-10-15T10:00:00Z",
        },
        "disclosures": {
            "recording_consent": True,
            "intake_consent": True,
            "intake_consent_at": "2026-10-15T10:00:15Z",
        },
        "intake": {
            "matter_type": "divorce_separation",
            "parties": [
                {
                    "role": "caller",
                    "name": "Asha Sharma",
                    "relationship_to_caller": "spouse",
                }
            ],
            "facts": [{"field_name": "separation_date", "value": "1 October 2026"}],
            "transcript_segments": [
                {
                    "speaker": "caller",
                    "text": "I need help with a separation.",
                    "started_at_ms": 0,
                    "ended_at_ms": 2000,
                }
            ],
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
    assert document["telephony"]["caller_phone_encrypted"].startswith("fernet:v1:")
    assert "+919999999999" not in str(document)
    assert "Asha Sharma" not in str(document)
    assert "I need help with a separation." not in str(document)
    assert encryptor.decrypt(document["intake"]["facts"][0]["value_encrypted"]) == "1 October 2026"
    assert response.json()["lawyer_review"]["status"] == "pending"
    assert [event["event_type"] for event in audit_collection.documents] == [
        "intake_created",
        "consent_recorded",
        "artifact_attached",
    ]
    assert "+919999999999" not in str(audit_collection.documents)
    assert "Asha Sharma" not in str(audit_collection.documents)
    assert "recording.wav" not in str(audit_collection.documents)


def test_create_intake_call_rejects_case_facts_without_both_consents() -> None:
    app = create_app()
    collection = FakeIntakeCallsCollection()
    app.dependency_overrides[get_intake_calls_collection] = lambda: collection
    app.dependency_overrides[get_intake_audit_events_collection] = FakeAuditEventsCollection
    app.dependency_overrides[get_field_encryptor] = lambda: FieldEncryptor(
        Fernet.generate_key().decode()
    )
    payload = {
        "telephony": {
            "provider": "exotel",
            "provider_call_id": "call-unconsented",
            "caller_phone": "+919999999999",
            "started_at": "2026-10-15T10:00:00Z",
        },
        "disclosures": {"intake_consent": True},
        "intake": {"facts": [{"field_name": "example", "value": "private"}]},
    }
    with TestClient(app) as client:
        response = client.post("/api/v1/intake-calls", json=payload)
    assert response.status_code == 422
    assert collection.documents == []


def test_create_intake_call_rejects_non_private_storage_reference() -> None:
    app = create_app()
    collection = FakeIntakeCallsCollection()
    audit_collection = FakeAuditEventsCollection()
    app.dependency_overrides[get_intake_calls_collection] = lambda: collection
    app.dependency_overrides[get_intake_audit_events_collection] = lambda: audit_collection
    app.dependency_overrides[get_field_encryptor] = lambda: FieldEncryptor(
        Fernet.generate_key().decode()
    )
    payload = {
        "telephony": {
            "provider": "exotel",
            "provider_call_id": "call-002",
            "caller_phone": "+919999999999",
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
