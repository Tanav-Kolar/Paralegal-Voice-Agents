"""Local-only APIs that support recording-review development tooling."""

from datetime import datetime
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.core.config import Settings, get_settings
from app.db.mongodb import get_intake_calls_collection
from app.providers.contracts import ObjectStorageProvider
from app.providers.gcs import GoogleCloudStorageProvider
from app.security.access import demo_access_ready, has_access

router = APIRouter()


def review_scope(request: Request, settings: Settings = Depends(get_settings)) -> dict:
    """Return the Mongo filter for calls this caller may see, or hide the route.

    Local development sees every call; the hosted prototype sees demo calls only.
    """
    if settings.local_review_api_enabled and settings.app_env != "production":
        return {}
    if demo_access_ready(settings):
        if not has_access(request.cookies, settings):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Access code required")
        return {"telephony.provider": "demo"}
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


def _create_object_storage_provider(settings: Settings) -> ObjectStorageProvider:
    return GoogleCloudStorageProvider.from_settings(settings)


def _recording_artifacts(document: dict) -> list[tuple[int, dict]]:
    artifacts = document.get("intake", {}).get("artifacts", [])
    return [
        (index, artifact)
        for index, artifact in enumerate(artifacts)
        if artifact.get("kind") == "call_recording" and artifact.get("storage_provider") == "gcs"
    ]


def _isoformat(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _recording_label(storage_key: str) -> str:
    filename = PurePosixPath(storage_key).name.removesuffix(".wav")
    return filename.removeprefix("recording-").replace("-", " ").title() or "Recording"


@router.get("/intake-calls")
async def list_local_review_calls(
    scope: dict = Depends(review_scope),
    collection=Depends(get_intake_calls_collection),
) -> list[dict[str, object]]:
    """List masked intake calls for review, newest first."""
    cursor = collection.find(
        scope,
        {
            "id": 1,
            "status": 1,
            "telephony.caller_phone_masked": 1,
            "telephony.started_at": 1,
            "disclosures.recording_consent": 1,
            "intake.artifacts": 1,
        },
    ).sort("telephony.started_at", -1)
    documents = await cursor.to_list(length=None)
    calls: list[dict[str, object]] = []
    for document in documents:
        call_id = document.get("id")
        if not call_id:
            continue
        recordings = [
            {"index": index, "label": _recording_label(artifact["storage_key"])}
            for index, artifact in _recording_artifacts(document)
            if artifact.get("storage_key")
        ]
        calls.append(
            {
                "id": str(call_id),
                "status": document.get("status", "unknown"),
                "callerPhoneMasked": document.get("telephony", {}).get(
                    "caller_phone_masked", "Unavailable"
                ),
                "receivedAt": _isoformat(document.get("telephony", {}).get("started_at")),
                "recordingConsent": document.get("disclosures", {}).get(
                    "recording_consent", False
                ),
                "recordings": recordings,
            }
        )
    return calls


@router.get("/intake-calls/{intake_call_id}/recordings/{artifact_index}/playback")
async def redirect_to_local_recording(
    intake_call_id: UUID,
    artifact_index: int,
    scope: dict = Depends(review_scope),
    settings: Settings = Depends(get_settings),
    collection=Depends(get_intake_calls_collection),
) -> RedirectResponse:
    """Issue a short-lived private-object URL for a playback request."""
    if artifact_index < 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    document = await collection.find_one(
        {"id": str(intake_call_id), **scope}, {"intake.artifacts": 1}
    )
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found")
    artifacts = document.get("intake", {}).get("artifacts", [])
    if artifact_index >= len(artifacts):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    artifact = artifacts[artifact_index]
    if artifact.get("kind") != "call_recording" or artifact.get("storage_provider") != "gcs":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    try:
        storage_provider = _create_object_storage_provider(settings)
        download_url = await storage_provider.create_download_url(artifact["storage_key"])
    except (KeyError, OSError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recording playback is unavailable",
        ) from error
    return RedirectResponse(download_url)
