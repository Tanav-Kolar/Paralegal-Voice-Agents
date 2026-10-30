"""Authenticated intake review and report APIs."""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from google.api_core.exceptions import GoogleAPICallError
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.db.mongodb import (
    get_call_events_collection,
    get_intake_audit_events_collection,
    get_intake_calls_collection,
    get_portal_users_collection,
)
from app.providers.contracts import ObjectStorageProvider
from app.providers.gcs import GoogleCloudStorageProvider
from app.schemas.audit_event import IntakeAuditEventType
from app.schemas.intake_call import (
    ArtifactKind,
    GeneratedReport,
    ProtectedObjectReference,
    ReviewStatus,
)
from app.security.field_encryption import FieldEncryptor, get_optional_field_encryptor
from app.security.portal import PORTAL_COOKIE, current_user, require_role, session_email
from app.services.audit import record_audit_event
from app.services.brief import render_brief, triage

router = APIRouter()


def _user_scope(user: dict) -> dict:
    return {"telephony.provider": "demo"} if user["role"] == "demo_lawyer" else {}


def _review_collections(request: Request, settings: Settings, primary, scope: dict) -> list:
    """Include configured historical databases for unscoped portal sessions only."""
    collections = [primary]
    if not scope and session_email(settings, request.cookies.get(PORTAL_COOKIE)):
        for name in dict.fromkeys(settings.portal_call_databases):
            if name != settings.mongodb_database:
                collections.append(request.app.state.mongodb.client[name]["intake_calls"])
    return collections


async def _find_call(
    request: Request, settings: Settings, primary, query: dict, projection: dict
) -> tuple[object, dict | None]:
    for source in _review_collections(request, settings, primary, query):
        document = await source.find_one(query, projection)
        if document:
            return source, document
    return primary, None


async def review_scope(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Return the Mongo filter for calls this caller may see, or hide the route.

    Portal users see all calls; the trial lawyer sees demo calls only.
    """
    email = session_email(settings, request.cookies.get(PORTAL_COOKIE))
    if email:
        users = get_portal_users_collection(request)
        user = await users.find_one({"email": email, "active": True})
        if user and user.get("role") in {"admin", "lawyer", "reviewer", "demo_lawyer"}:
            return _user_scope(user)
    if settings.local_review_api_enabled and settings.app_env != "production":
        return {}
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


def _transcript(document: dict, encryptor: FieldEncryptor | None) -> tuple[list[dict], bool]:
    """Return the consented conversation in speaking order for lawyer review."""
    items = []
    unavailable = False
    for segment in document.get("intake", {}).get("transcript_segments", []):
        encrypted = segment.get("text_encrypted")
        try:
            text = encryptor.decrypt(encrypted) if encryptor and encrypted else None
        except ValueError:
            text = None
        if not text:
            unavailable = unavailable or bool(encrypted)
            continue
        items.append(
            {
                "id": str(segment.get("id", "")),
                "speaker": segment.get("speaker", "caller"),
                "text": text,
                "startedAtMs": int(segment.get("started_at_ms", 0)),
                "endedAtMs": int(segment.get("ended_at_ms", segment.get("started_at_ms", 0))),
                "language": segment.get("detected_language"),
            }
        )
    return sorted(items, key=lambda item: (item["startedAtMs"], item["id"])), unavailable


@router.get("/events")
async def list_call_events(
    request: Request,
    user: dict = Depends(current_user),
    settings: Settings = Depends(get_settings),
    event_collection=Depends(get_call_events_collection),
) -> list[dict[str, object]]:
    """Group lifecycle events by the full stream or provider call identifier."""
    sources = [(settings.mongodb_database, event_collection)]
    for name in dict.fromkeys(settings.portal_call_databases):
        if name != settings.mongodb_database and user["role"] != "demo_lawyer":
            sources.append((name, request.app.state.mongodb.client[name]["call_events"]))
    sessions: dict[str, dict] = {}
    for database_name, source in sources:
        cursor = source.find(
            {"provider": "demo"} if user["role"] == "demo_lawyer" else {},
            {
                "_id": 0,
                "event_type": 1,
                "received_at": 1,
                "stream_sid": 1,
                "provider_call_id": 1,
                "event_key": 1,
            },
        ).sort("received_at", -1)
        for index, item in enumerate(await cursor.to_list(length=500)):
            identifier = (
                item.get("stream_sid")
                or item.get("provider_call_id")
                or item.get("event_key")
                or f"event-{index}"
            )
            key = f"{database_name}:{identifier}"
            if key not in sessions:
                sessions[key] = {
                    "id": sha256(key.encode()).hexdigest()[:16],
                    "events": [],
                }
            sessions[key]["events"].append(
                {
                    "type": item.get("event_type", "unknown"),
                    "receivedAt": _isoformat(item.get("received_at")),
                }
            )
    result = []
    for session in sessions.values():
        ordered = sorted(session["events"], key=lambda event: event["receivedAt"] or "")
        session["events"] = ordered
        session["startedAt"] = ordered[0]["receivedAt"]
        session["lastAt"] = ordered[-1]["receivedAt"]
        session["lastEventType"] = ordered[-1]["type"]
        result.append(session)
    return sorted(result, key=lambda session: session["lastAt"] or "", reverse=True)[:200]


@router.get("/intake-calls")
async def list_local_review_calls(
    request: Request,
    scope: dict = Depends(review_scope),
    settings: Settings = Depends(get_settings),
    encryptor: FieldEncryptor | None = Depends(get_optional_field_encryptor),
    collection=Depends(get_intake_calls_collection),
) -> list[dict[str, object]]:
    """List masked intake calls for review, newest first."""
    documents = []
    for source in _review_collections(request, settings, collection, scope):
        cursor = source.find(
            scope,
            {
                "id": 1,
                "status": 1,
                "telephony.caller_phone_masked": 1,
                "telephony.started_at": 1,
                "disclosures.recording_consent": 1,
                "intake.artifacts": 1,
                "intake.matter_type": 1,
                "intake.facts": 1,
                "intake.transcript_segments": 1,
                "intake.reports": 1,
                "lawyer_review.status": 1,
            },
        ).sort("telephony.started_at", -1)
        documents.extend(await cursor.to_list(length=None))
    calls: list[dict[str, object]] = []
    named_portal_user = bool(session_email(settings, request.cookies.get(PORTAL_COOKIE)))
    for document in documents:
        call_id = document.get("id")
        if not call_id:
            continue
        recordings = [
            {"index": index, "label": _recording_label(artifact["storage_key"])}
            for index, artifact in _recording_artifacts(document)
            if artifact.get("storage_key")
        ]
        summary = triage(document, encryptor)
        caller_name = next(
            (
                fact["value"]
                for fact in summary["facts"]
                if fact["field"].casefold() == "caller name" and fact["value"]
            ),
            None,
        )
        calls.append(
            {
                "id": str(call_id),
                "status": document.get("status", "unknown"),
                "callerPhoneMasked": document.get("telephony", {}).get(
                    "caller_phone_masked", "Unavailable"
                ),
                "receivedAt": _isoformat(document.get("telephony", {}).get("started_at")),
                "recordingConsent": document.get("disclosures", {}).get("recording_consent", False),
                "recordings": recordings,
                "matterType": summary["matterType"],
                "callerName": caller_name if named_portal_user else None,
                "reviewStatus": document.get("lawyer_review", {}).get("status", "pending"),
                "reportCount": len(document.get("intake", {}).get("reports", [])),
            }
        )
    return sorted(calls, key=lambda call: call["receivedAt"] or "", reverse=True)


@router.get("/intake-calls/{intake_call_id}/recordings/{artifact_index}/playback")
async def stream_recording(
    request: Request,
    intake_call_id: UUID,
    artifact_index: int,
    scope: dict = Depends(review_scope),
    settings: Settings = Depends(get_settings),
    collection=Depends(get_intake_calls_collection),
) -> Response:
    """Serve a consented recording through the authenticated review endpoint."""
    if artifact_index < 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    _, document = await _find_call(
        request,
        settings,
        collection,
        {"id": str(intake_call_id), **scope},
        {"intake.artifacts": 1, "disclosures.recording_consent": 1},
    )
    if not document:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Call not found")
    if document.get("disclosures", {}).get("recording_consent") is not True:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    artifacts = document.get("intake", {}).get("artifacts", [])
    if artifact_index >= len(artifacts):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    artifact = artifacts[artifact_index]
    if artifact.get("kind") != "call_recording" or artifact.get("storage_provider") != "gcs":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    try:
        storage_provider = _create_object_storage_provider(settings)
        audio = await storage_provider.load(artifact["storage_key"])
    except (GoogleAPICallError, KeyError, OSError, ValueError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recording playback is unavailable",
        ) from error
    return Response(audio, media_type="audio/wav", headers={"Cache-Control": "private, no-store"})


class ReviewUpdate(BaseModel):
    status: ReviewStatus
    notes: str = Field(default="", max_length=10000)


@router.get("/intake-calls/{intake_call_id}")
async def get_review_call(
    request: Request,
    intake_call_id: UUID,
    user: dict = Depends(current_user),
    settings: Settings = Depends(get_settings),
    encryptor: FieldEncryptor | None = Depends(get_optional_field_encryptor),
    collection=Depends(get_intake_calls_collection),
    event_collection=Depends(get_call_events_collection),
) -> dict:
    source, document = await _find_call(
        request, settings, collection, {"id": str(intake_call_id), **_user_scope(user)}, {"_id": 0}
    )
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    review = document.get("lawyer_review", {})
    notes = review.get("internal_notes_encrypted")
    try:
        decrypted_notes = encryptor.decrypt(notes) if notes and encryptor else ""
    except ValueError:
        decrypted_notes = ""
    summary = triage(document, encryptor)
    transcript, transcript_unavailable = _transcript(document, encryptor)
    if notes and not decrypted_notes:
        summary["contentUnavailable"] = True
    if transcript_unavailable:
        summary["contentUnavailable"] = True
    events = []
    stream_sid = document.get("telephony", {}).get("stream_sid")
    if stream_sid:
        events_source = event_collection
        if source is not collection:
            events_source = request.app.state.mongodb.client[source.database.name]["call_events"]
        cursor = events_source.find(
            {"stream_sid": stream_sid},
            {"_id": 0, "event_type": 1, "received_at": 1},
        ).sort("received_at", 1)
        events = [
            {
                "type": item.get("event_type", "unknown"),
                "receivedAt": _isoformat(item.get("received_at")),
            }
            for item in await cursor.to_list(length=200)
        ]
    return {
        "id": str(intake_call_id),
        "summary": summary,
        "reviewStatus": review.get("status", "pending"),
        "notes": decrypted_notes,
        "transcript": transcript,
        "events": events,
        "reports": [
            {"version": report["version"], "generatedAt": _isoformat(report.get("generated_at"))}
            for report in document.get("intake", {}).get("reports", [])
        ],
    }


@router.put("/intake-calls/{intake_call_id}/review")
async def save_review(
    request: Request,
    intake_call_id: UUID,
    body: ReviewUpdate,
    user: dict = Depends(require_role("admin", "lawyer", "reviewer", "demo_lawyer")),
    settings: Settings = Depends(get_settings),
    encryptor: FieldEncryptor | None = Depends(get_optional_field_encryptor),
    collection=Depends(get_intake_calls_collection),
    audit_collection=Depends(get_intake_audit_events_collection),
) -> dict:
    source, existing = await _find_call(
        request,
        settings,
        collection,
        {"id": str(intake_call_id), **_user_scope(user)},
        {"lawyer_review.status": 1, "lawyer_review.internal_notes_encrypted": 1},
    )
    if not existing:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    if body.notes and not encryptor:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "A valid FIELD_ENCRYPTION_KEY is required to save notes",
        )
    now = datetime.now(UTC)
    update_fields = {
        "lawyer_review.status": body.status.value,
        "lawyer_review.reviewed_at": now.isoformat()
        if body.status != ReviewStatus.PENDING
        else None,
        "updated_at": now.isoformat(),
    }
    if body.notes and encryptor:
        update_fields["lawyer_review.internal_notes_encrypted"] = (
            encryptor.encrypt(body.notes) if body.notes else None
        )
    elif encryptor and existing.get("lawyer_review", {}).get("internal_notes_encrypted"):
        try:
            encryptor.decrypt(existing["lawyer_review"]["internal_notes_encrypted"])
            update_fields["lawyer_review.internal_notes_encrypted"] = None
        except ValueError:
            pass
    result = await source.update_one(
        {"id": str(intake_call_id), **_user_scope(user)},
        {"$set": update_fields},
    )
    if not result.matched_count:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    audit_source = audit_collection
    if source is not collection:
        audit_source = request.app.state.mongodb.client[source.database.name]["intake_audit_events"]
    if existing.get("lawyer_review", {}).get("status", "pending") != body.status.value:
        await record_audit_event(
            audit_source,
            intake_call_id=intake_call_id,
            event_type=IntakeAuditEventType.REVIEW_STATUS_CHANGED,
            actor_type="portal_user",
            details={"status": body.status.value, "actor": user["email"]},
        )
    if "lawyer_review.internal_notes_encrypted" in update_fields:
        await record_audit_event(
            audit_source,
            intake_call_id=intake_call_id,
            event_type=IntakeAuditEventType.REVIEW_NOTES_SAVED,
            actor_type="portal_user",
            details={"actor": user["email"]},
        )
    return {"saved": True, "reviewStatus": body.status.value}


@router.post("/intake-calls/{intake_call_id}/reports", status_code=201)
async def generate_report(
    request: Request,
    intake_call_id: UUID,
    user: dict = Depends(require_role("admin", "lawyer", "demo_lawyer")),
    settings: Settings = Depends(get_settings),
    encryptor: FieldEncryptor | None = Depends(get_optional_field_encryptor),
    collection=Depends(get_intake_calls_collection),
    audit_collection=Depends(get_intake_audit_events_collection),
) -> dict:
    source, document = await _find_call(
        request, settings, collection, {"id": str(intake_call_id), **_user_scope(user)}, {"_id": 0}
    )
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    if document.get("disclosures", {}).get("intake_consent") is not True:
        raise HTTPException(status.HTTP_409_CONFLICT, "Intake consent is required")
    current = max(
        (int(r.get("version", 0)) for r in document.get("intake", {}).get("reports", [])), default=0
    )
    version = current + 1
    summary = triage(document, encryptor)
    if summary["contentUnavailable"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "The original FIELD_ENCRYPTION_KEY is required for this brief",
        )
    pdf = render_brief(document, summary, version, settings.firm_name)
    try:
        storage = _create_object_storage_provider(settings)
        stored = await storage.store(
            intake_call_id, f"brief-v{version}.pdf", pdf, "application/pdf"
        )
    except (OSError, ValueError) as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "PDF storage is unavailable"
        ) from error
    report = GeneratedReport(
        version=version,
        artifact=ProtectedObjectReference(
            kind=ArtifactKind.INTAKE_REPORT,
            storage_provider="gcs",
            storage_key=stored.key,
            content_type=stored.content_type,
        ),
    )
    result = await source.update_one(
        {
            "id": str(intake_call_id),
            **_user_scope(user),
            "intake.reports.version": {"$ne": version},
        },
        {"$push": {"intake.reports": report.model_dump(mode="json")}},
    )
    if not result.modified_count:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A report version was created concurrently; retry"
        )
    audit_source = audit_collection
    if source is not collection:
        audit_source = request.app.state.mongodb.client[source.database.name]["intake_audit_events"]
    await record_audit_event(
        audit_source,
        intake_call_id=intake_call_id,
        event_type=IntakeAuditEventType.REPORT_GENERATED,
        actor_type="portal_user",
        details={"version": version, "actor": user["email"]},
    )
    return {"version": version, "generatedAt": report.generated_at.isoformat()}


@router.get("/intake-calls/{intake_call_id}/reports/{version}/download")
async def download_report(
    request: Request,
    intake_call_id: UUID,
    version: int,
    user: dict = Depends(current_user),
    settings: Settings = Depends(get_settings),
    collection=Depends(get_intake_calls_collection),
) -> Response:
    _, document = await _find_call(
        request,
        settings,
        collection,
        {"id": str(intake_call_id), **_user_scope(user)},
        {"intake.reports": 1},
    )
    if not document:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Call not found")
    report = next(
        (
            item
            for item in document.get("intake", {}).get("reports", [])
            if item.get("version") == version
        ),
        None,
    )
    if not report or report.get("artifact", {}).get("kind") != "intake_report":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    try:
        storage = _create_object_storage_provider(settings)
        pdf = await storage.load(report["artifact"]["storage_key"])
    except (GoogleAPICallError, OSError, ValueError, KeyError) as error:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "PDF download is unavailable"
        ) from error
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="intake-brief-v{version}.pdf"',
            "Cache-Control": "private, no-store",
        },
    )
