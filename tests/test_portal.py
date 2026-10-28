from copy import deepcopy
from datetime import UTC, datetime, timedelta
from io import BytesIO
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app.api.routes import review_api
from app.core.config import Settings, get_settings
from app.db.mongodb import (
    get_call_events_collection,
    get_intake_audit_events_collection,
    get_intake_calls_collection,
    get_portal_users_collection,
)
from app.main import create_app
from app.security.field_encryption import FieldEncryptor
from app.security.portal import hash_password


class Users:
    def __init__(self):
        self.items = {
            "lawyer@example.com": {
                "email": "lawyer@example.com",
                "name": "A Lawyer",
                "role": "lawyer",
                "active": True,
                "password_hash": hash_password("correct long password"),
            },
            "reviewer@example.com": {
                "email": "reviewer@example.com",
                "name": "A Reviewer",
                "role": "reviewer",
                "active": True,
                "password_hash": hash_password("correct long password"),
            },
        }

    async def find_one(self, query):
        user = self.items.get(query["email"])
        return user if user and user["active"] else None


class Audit:
    def __init__(self):
        self.events = []

    async def insert_one(self, event):
        self.events.append(event)


class EventCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, key, direction):
        return self

    async def to_list(self, length):
        return self.documents[:length]


class Events:
    def __init__(self):
        started = datetime(2026, 10, 22, 9, 40, tzinfo=UTC)
        self.documents = [
            {
                "stream_sid": "stream-one",
                "event_type": "voicebot_stream_disconnected",
                "received_at": started + timedelta(minutes=2),
            },
            {
                "stream_sid": "stream-one",
                "event_type": "voicebot_stream_started",
                "received_at": started,
            },
            {
                "stream_sid": "stream-two",
                "event_type": "voicebot_stream_started",
                "received_at": started - timedelta(days=1),
            },
            {
                "stream_sid": "stream-one",
                "event_type": "voicebot_consent_granted",
                "received_at": started + timedelta(minutes=1),
            },
        ]

    def find(self, query, projection):
        return EventCursor(
            [
                item
                for item in self.documents
                if not query or item["stream_sid"] == query.get("stream_sid")
            ]
        )


class Result:
    matched_count = 1
    modified_count = 1


class Calls:
    def __init__(self, encryptor):
        self.id = str(uuid4())
        self.document = {
            "id": self.id,
            "status": "completed",
            "telephony": {
                "provider": "exotel",
                "stream_sid": "stream-one",
                "caller_phone_masked": "******1234",
                "started_at": datetime.now(UTC),
            },
            "disclosures": {"intake_consent": True, "recording_consent": True},
            "language": {"preferred": "hindi"},
            "intake": {
                "matter_type": "family",
                "reports": [],
                "artifacts": [],
                "facts": [
                    {
                        "field_name": "city",
                        "value_encrypted": encryptor.encrypt("Delhi"),
                    }
                ],
                "transcript_segments": [
                    {
                        "id": str(uuid4()),
                        "speaker": "caller",
                        "text_encrypted": encryptor.encrypt("मुझे वकील से बात करनी है।"),
                    }
                ],
            },
            "lawyer_review": {"status": "pending", "internal_notes_encrypted": None},
        }

    async def find_one(self, query, projection):
        return deepcopy(self.document) if query.get("id") == self.id else None

    def find(self, query, projection):
        class Cursor:
            def sort(self, key, direction):
                return self

            async def to_list(self, length):
                return [deepcopy(self_document)]

        self_document = self.document
        return Cursor()

    async def update_one(self, query, update):
        if query.get("id") != self.id:
            result = Result()
            result.matched_count = 0
            result.modified_count = 0
            return result
        if "$set" in update:
            for key, value in update["$set"].items():
                target = self.document
                parts = key.split(".")
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = value
        if "$push" in update:
            version = query["intake.reports.version"]["$ne"]
            if any(item["version"] == version for item in self.document["intake"]["reports"]):
                result = Result()
                result.modified_count = 0
                return result
            self.document["intake"]["reports"].append(update["$push"]["intake.reports"])
        return Result()


class Storage:
    def __init__(self):
        self.pdfs = {}

    async def store(self, call_id, filename, content, content_type):
        assert content_type == "application/pdf"
        self.pdfs[filename] = content
        return type(
            "Stored",
            (),
            {"key": f"intake-calls/{call_id}/{filename}", "content_type": content_type},
        )()

    async def create_download_url(self, key, *, filename=None):
        assert filename and filename.endswith(".pdf")
        return f"https://storage.example/{key}"

    async def load(self, key):
        return self.pdfs[key.rsplit("/", 1)[-1]]


def setup(monkeypatch):
    key = Fernet.generate_key().decode()
    settings = Settings(
        _env_file=None,
        app_env="production",
        portal_session_secret="s" * 32,
        field_encryption_key=key,
    )
    users, calls, audit, storage = (
        Users(),
        Calls(FieldEncryptor(key)),
        Audit(),
        Storage(),
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_portal_users_collection] = lambda: users
    app.dependency_overrides[get_call_events_collection] = lambda: Events()
    app.dependency_overrides[get_intake_calls_collection] = lambda: calls
    app.dependency_overrides[get_intake_audit_events_collection] = lambda: audit
    monkeypatch.setattr(review_api, "_create_object_storage_provider", lambda settings: storage)
    return (
        TestClient(app, base_url="https://testserver"),
        users,
        calls,
        audit,
        storage,
        key,
    )


def test_portal_sign_in_review_and_versioned_pdf(monkeypatch):
    client, _users, calls, audit, storage, key = setup(monkeypatch)
    path = f"/api/v1/review/intake-calls/{calls.id}"
    with client:
        assert client.get(path).status_code == 401
        assert (
            client.post(
                "/api/v1/portal/login",
                json={"email": "lawyer@example.com", "password": "wrong"},
            ).status_code
            == 401
        )
        login = client.post(
            "/api/v1/portal/login",
            json={"email": "LAWYER@example.com", "password": "correct long password"},
        )
        assert login.status_code == 200
        assert login.json()["role"] == "lawyer"
        detail = client.get(path).json()
        assert detail["summary"]["facts"][0]["value"] == "Delhi"
        assert detail["summary"]["overview"] == "A triage summary is not available for this call."
        assert detail["summary"]["matterType"] == "Other family matter"
        assert detail["transcript"] == [
            {
                "id": detail["transcript"][0]["id"],
                "speaker": "caller",
                "text": "मुझे वकील से बात करनी है।",
                "startedAtMs": 0,
                "endedAtMs": 0,
                "language": None,
            }
        ]
        assert len(detail["events"]) == 3
        sessions = client.get("/api/v1/review/events").json()
        assert len(sessions) == 2
        assert [event["type"] for event in sessions[0]["events"]] == [
            "voicebot_stream_started",
            "voicebot_consent_granted",
            "voicebot_stream_disconnected",
        ]
        assert sessions[0]["lastEventType"] == "voicebot_stream_disconnected"
        assert sessions[0]["id"] != sessions[1]["id"]
        assert (
            client.put(
                path + "/review",
                json={"status": "reviewed", "notes": "Call back tomorrow"},
            ).status_code
            == 200
        )
        assert (
            FieldEncryptor(key).decrypt(calls.document["lawyer_review"]["internal_notes_encrypted"])
            == "Call back tomorrow"
        )
        assert client.post(path + "/reports").json()["version"] == 1
        assert client.post(path + "/reports").json()["version"] == 2
        assert [item["version"] for item in calls.document["intake"]["reports"]] == [
            1,
            2,
        ]
        download = client.get(path + "/reports/1/download")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/pdf"
        assert download.content == storage.pdfs["brief-v1.pdf"]
        assert client.post("/api/v1/portal/logout").status_code == 200
        assert client.get(path).status_code == 401
    pdf = PdfReader(BytesIO(storage.pdfs["brief-v1.pdf"]))
    text = " ".join(page.extract_text() for page in pdf.pages)
    assert "Version 1" in text and "unverified" in text
    assert "Case Fact Sheet" in text
    assert {event["event_type"] for event in audit.events} == {
        "review_status_changed",
        "review_notes_saved",
        "report_generated",
    }


def test_portal_queue_shows_corrected_caller_name(monkeypatch):
    client, users, calls, _audit, _storage, key = setup(monkeypatch)
    monkeypatch.setattr(review_api, "get_portal_users_collection", lambda request: users)
    calls.document["intake"]["facts"].append(
        {
            "field_name": "Caller name",
            "value_encrypted": FieldEncryptor(key).encrypt("Asha Mehta"),
            "provided_by_caller": False,
            "not_provided": False,
            "source_transcript_segment_ids": [],
        }
    )
    with client:
        client.post(
            "/api/v1/portal/login",
            json={"email": "lawyer@example.com", "password": "correct long password"},
        )
        queue = client.get("/api/v1/review/intake-calls").json()
        detail = client.get(f"/api/v1/review/intake-calls/{calls.id}").json()
    assert queue[0]["callerName"] == "Asha Mehta"
    assert next(
        fact for fact in detail["summary"]["facts"] if fact["field"] == "Caller name"
    )["providedByCaller"] is False


def test_reviewer_cannot_generate_brief(monkeypatch):
    client, _users, calls, _audit, _storage, _key = setup(monkeypatch)
    with client:
        client.post(
            "/api/v1/portal/login",
            json={"email": "reviewer@example.com", "password": "correct long password"},
        )
        assert client.post(f"/api/v1/review/intake-calls/{calls.id}/reports").status_code == 403
