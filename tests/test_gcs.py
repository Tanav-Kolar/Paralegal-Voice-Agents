from unittest.mock import patch
from uuid import UUID

import pytest

from app.core.config import Settings
from app.providers.gcs import GoogleCloudStorageProvider


class FakeBlob:
    def __init__(self, key: str) -> None:
        self.key = key
        self.uploads: list[tuple[bytes, str]] = []
        self.signed_url_options: dict | None = None

    def upload_from_string(self, content: bytes, content_type: str) -> None:
        self.uploads.append((content, content_type))

    def generate_signed_url(self, **kwargs) -> str:
        self.signed_url_options = kwargs
        assert kwargs["version"] == "v4"
        assert kwargs["method"] == "GET"
        return f"https://storage.example.test/{self.key}?signature=temporary"

    def download_as_bytes(self) -> bytes:
        return b"stored-object"


class FakeBucket:
    def __init__(self) -> None:
        self.blobs: dict[str, FakeBlob] = {}

    def blob(self, key: str) -> FakeBlob:
        return self.blobs.setdefault(key, FakeBlob(key))


class FakeStorageClient:
    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket())


@pytest.mark.asyncio
async def test_gcs_provider_stores_private_object_and_generates_signed_url() -> None:
    client = FakeStorageClient()
    provider = GoogleCloudStorageProvider("demo-private-bucket", 10, client)  # type: ignore[arg-type]
    intake_call_id = UUID("11111111-1111-1111-1111-111111111111")

    stored = await provider.store(
        intake_call_id,
        "../recording.wav",
        b"audio-data",
        "audio/wav",
    )
    url = await provider.create_download_url(stored.key)

    assert stored.key == "intake-calls/11111111-1111-1111-1111-111111111111/recording.wav"
    assert stored.content_type == "audio/wav"
    assert client.bucket("demo-private-bucket").blobs[stored.key].uploads == [
        (b"audio-data", "audio/wav")
    ]
    assert url == "https://storage.example.test/" f"{stored.key}?signature=temporary"


@pytest.mark.asyncio
async def test_gcs_provider_rejects_unsafe_download_key() -> None:
    provider = GoogleCloudStorageProvider("demo-private-bucket", 10, FakeStorageClient())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Storage key is invalid"):
        await provider.create_download_url("../recording.wav")


def test_gcs_provider_uses_application_default_credentials_without_key_file() -> None:
    settings = Settings(
        _env_file=None,
        object_storage_provider="gcs",
        gcs_project_id="demo-project",
        gcs_bucket_name="demo-private-bucket",
    )

    with patch("app.providers.gcs.storage.Client") as client_factory:
        GoogleCloudStorageProvider.from_settings(settings)

    client_factory.assert_called_once_with(project="demo-project")


@pytest.mark.asyncio
async def test_gcs_provider_reads_private_object_for_authenticated_playback() -> None:
    client = FakeStorageClient()
    provider = GoogleCloudStorageProvider("demo-private-bucket", 10, client)  # type: ignore[arg-type]

    content = await provider.load("intake-calls/call/recording.wav")

    assert content == b"stored-object"
    with pytest.raises(ValueError, match="Storage key is invalid"):
        await provider.load("../recording.wav")
