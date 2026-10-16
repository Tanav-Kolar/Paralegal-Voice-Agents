"""Private Google Cloud Storage adapter for protected intake artifacts."""

import asyncio
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from google.cloud import storage

from app.core.config import Settings
from app.providers.contracts import StoredObject


class GoogleCloudStorageProvider:
    """Store private artifacts and issue short-lived download URLs on demand."""

    def __init__(
        self,
        bucket_name: str,
        signed_url_ttl_minutes: int,
        client: storage.Client,
    ) -> None:
        self._bucket_name = bucket_name
        self._signed_url_ttl = timedelta(minutes=signed_url_ttl_minutes)
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "GoogleCloudStorageProvider":
        if settings.object_storage_provider != "gcs":
            raise ValueError(
                "Google Cloud Storage must be configured as the object-storage provider"
            )
        if not settings.gcs_bucket_name:
            raise ValueError("GCS bucket name is not configured")
        if settings.gcs_service_account_key_path:
            credentials_path = Path(settings.gcs_service_account_key_path)
            if not credentials_path.is_file():
                raise ValueError("GCS service-account key file was not found")
            client = storage.Client.from_service_account_json(
                str(credentials_path),
                project=settings.gcs_project_id or None,
            )
        else:
            client = storage.Client(project=settings.gcs_project_id or None)
        return cls(
            bucket_name=settings.gcs_bucket_name,
            signed_url_ttl_minutes=settings.gcs_signed_url_ttl_minutes,
            client=client,
        )

    async def store(
        self,
        intake_call_id: UUID,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> StoredObject:
        safe_filename = Path(filename).name
        if not safe_filename or safe_filename == ".":
            raise ValueError("Storage filename is required")
        key = f"intake-calls/{intake_call_id}/{safe_filename}"
        await asyncio.to_thread(self._upload, key, content, content_type)
        return StoredObject(key=key, content_type=content_type)

    async def create_download_url(self, key: str) -> str:
        if not key or key.startswith("/") or ".." in Path(key).parts:
            raise ValueError("Storage key is invalid")
        return await asyncio.to_thread(self._signed_download_url, key)

    def _upload(self, key: str, content: bytes, content_type: str) -> None:
        blob = self._client.bucket(self._bucket_name).blob(key)
        blob.upload_from_string(content, content_type=content_type)

    def _signed_download_url(self, key: str) -> str:
        blob = self._client.bucket(self._bucket_name).blob(key)
        return blob.generate_signed_url(
            version="v4",
            expiration=self._signed_url_ttl,
            method="GET",
        )
