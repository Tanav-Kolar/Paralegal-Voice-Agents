from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.routes import review_api
from app.core.config import Settings, get_settings
from app.db.mongodb import get_intake_calls_collection
from app.main import create_app


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def sort(self, key: str, direction: int) -> "FakeCursor":
        def nested_value(document: dict) -> object:
            value: object = document
            for part in key.split("."):
                if not isinstance(value, dict):
                    return ""
                value = value.get(part, "")
            return value

        self.documents.sort(key=nested_value, reverse=direction < 0)
        return self

    async def to_list(self, length: int | None) -> list[dict]:
        return self.documents if length is None else self.documents[:length]


class FakeIntakeCallsCollection:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def find(self, _filter: dict, _projection: dict) -> FakeCursor:
        return FakeCursor(list(self.documents))

    async def find_one(self, filter: dict, _projection: dict) -> dict | None:
        return next((item for item in self.documents if item["id"] == filter["id"]), None)


class FakeStorageProvider:
    async def create_download_url(self, key: str) -> str:
        return f"https://storage.example.test/{key}?signature=temporary"


def make_client(*, app_env: str = "development") -> TestClient:
    settings = Settings(
        _env_file=None,
        app_env=app_env,
        local_review_api_enabled=True,
        object_storage_provider="gcs",
        gcs_bucket_name="demo-private-bucket",
    )
    older_id = uuid4()
    newer_id = uuid4()
    now = datetime.now(UTC)
    collection = FakeIntakeCallsCollection(
        [
            {
                "id": str(older_id),
                "status": "completed",
                "telephony": {
                    "caller_phone_masked": "+91******1234",
                    "started_at": now - timedelta(minutes=5),
                },
                "disclosures": {"recording_consent": True},
                "intake": {
                    "artifacts": [
                        {
                            "kind": "call_recording",
                            "storage_provider": "gcs",
                            "storage_key": f"intake-calls/{older_id}/recording-caller.wav",
                            "content_type": "audio/wav",
                        }
                    ]
                },
            },
            {
                "id": str(newer_id),
                "status": "in_progress",
                "telephony": {
                    "caller_phone_masked": "+91******5678",
                    "started_at": now,
                },
                "disclosures": {"recording_consent": False},
                "intake": {"artifacts": []},
            },
        ]
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_intake_calls_collection] = lambda: collection
    return TestClient(app)


def test_local_review_api_lists_masked_calls_newest_first() -> None:
    client = make_client()

    with client:
        response = client.get("/api/v1/review/intake-calls")

    assert response.status_code == 200
    calls = response.json()
    assert [call["status"] for call in calls] == ["in_progress", "completed"]
    assert calls[1]["callerPhoneMasked"] == "+91******1234"
    assert calls[1]["recordings"] == [{"index": 0, "label": "Caller"}]


def test_local_review_api_redirects_to_signed_recording_url(monkeypatch) -> None:
    monkeypatch.setattr(
        review_api,
        "_create_object_storage_provider",
        lambda settings: FakeStorageProvider(),
    )
    client = make_client()

    with client:
        call_id = client.get("/api/v1/review/intake-calls").json()[1]["id"]
        response = client.get(
            f"/api/v1/review/intake-calls/{call_id}/recordings/0/playback",
            follow_redirects=False,
        )

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://storage.example.test/intake-calls/")


def test_local_review_api_is_unavailable_in_production() -> None:
    client = make_client(app_env="production")

    with client:
        response = client.get("/api/v1/review/intake-calls")

    assert response.status_code == 404
