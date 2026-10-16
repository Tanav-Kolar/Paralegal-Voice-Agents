"""Local-only recording review UI for development and demo verification."""

from datetime import datetime
from pathlib import PurePosixPath
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import Settings, get_settings
from app.db.mongodb import get_intake_calls_collection
from app.providers.contracts import ObjectStorageProvider
from app.providers.gcs import GoogleCloudStorageProvider

router = APIRouter()
ui_router = APIRouter(include_in_schema=False)


REVIEW_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Voice Intake Review</title>
    <style>
      :root { color-scheme: light; font-family: Inter, system-ui, sans-serif; }
      body { background: #f6f7fb; color: #172033; margin: 0; }
      main { margin: 3rem auto; max-width: 1050px; padding: 0 1.25rem; }
      h1 { margin-bottom: .25rem; } .notice { color: #56637b; margin-top: 0; }
      .panel { background: white; border: 1px solid #dde3ee; border-radius: 12px; overflow: hidden; }
      table { border-collapse: collapse; width: 100%; } th, td { border-bottom: 1px solid #edf0f5; padding: .9rem; text-align: left; vertical-align: middle; }
      th { background: #f8f9fc; color: #56637b; font-size: .78rem; letter-spacing: .04em; text-transform: uppercase; }
      .status { font-weight: 600; } button { background: #2155c5; border: 0; border-radius: 6px; color: white; cursor: pointer; margin: .15rem; padding: .45rem .7rem; }
      button:hover { background: #173f96; } .empty, .error { padding: 1.2rem; color: #56637b; } .error { color: #a31621; }
      @media (max-width: 700px) { table { font-size: .85rem; } th, td { padding: .55rem; } }
    </style>
  </head>
  <body>
    <main>
      <h1>Voice Intake Review</h1>
      <p class="notice">Local testing only. Calls are shown newest first; caller numbers remain masked.</p>
      <section class="panel" id="content"><p class="empty">Loading calls…</p></section>
    </main>
    <script>
      const content = document.getElementById("content");
      let activeAudio = null;

      function text(value) { return document.createTextNode(value || "—"); }
      function formatDate(value) { return value ? new Date(value).toLocaleString() : "—"; }

      function playRecording(callId, index) {
        if (activeAudio) activeAudio.pause();
        activeAudio = new Audio(`/api/v1/review/intake-calls/${callId}/recordings/${index}/playback`);
        activeAudio.play().catch(() => window.alert("Unable to start playback."));
      }

      function recordingButtons(call) {
        const container = document.createElement("div");
        if (!call.recordings.length) { container.append(text("No post-consent recording")); return container; }
        call.recordings.forEach((recording) => {
          const button = document.createElement("button");
          button.type = "button";
          button.textContent = `Play ${recording.label}`;
          button.addEventListener("click", () => playRecording(call.id, recording.index));
          container.append(button);
        });
        return container;
      }

      fetch("/api/v1/review/intake-calls")
        .then((response) => response.ok ? response.json() : Promise.reject())
        .then((calls) => {
          if (!calls.length) { content.innerHTML = '<p class="empty">No calls have been recorded yet.</p>'; return; }
          const table = document.createElement("table");
          const header = document.createElement("tr");
          ["Received", "Caller", "Status", "Consent", "Recordings"].forEach((title) => {
            const cell = document.createElement("th"); cell.textContent = title; header.append(cell);
          });
          const head = document.createElement("thead"); head.append(header); table.append(head);
          const body = document.createElement("tbody");
          calls.forEach((call) => {
            const row = document.createElement("tr");
            const received = document.createElement("td"); received.append(text(formatDate(call.receivedAt)));
            const caller = document.createElement("td"); caller.append(text(call.callerPhoneMasked));
            const status = document.createElement("td"); status.className = "status"; status.append(text(call.status));
            const consent = document.createElement("td"); consent.append(text(call.recordingConsent ? "Granted" : "Not granted"));
            const recordings = document.createElement("td"); recordings.append(recordingButtons(call));
            [received, caller, status, consent, recordings].forEach((cell) => row.append(cell));
            body.append(row);
          });
          table.append(body); content.replaceChildren(table);
        })
        .catch(() => { content.innerHTML = '<p class="error">Unable to load local review data.</p>'; });
    </script>
  </body>
</html>"""


def _require_local_review_ui(settings: Settings) -> None:
    if not settings.local_review_ui_enabled or settings.app_env == "production":
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


@ui_router.get("/review", response_class=HTMLResponse)
async def local_review_page(settings: Settings = Depends(get_settings)) -> HTMLResponse:
    """Render the development-only recording review page."""
    _require_local_review_ui(settings)
    return HTMLResponse(REVIEW_PAGE)


@router.get("/intake-calls")
async def list_local_review_calls(
    settings: Settings = Depends(get_settings),
    collection=Depends(get_intake_calls_collection),
) -> list[dict[str, object]]:
    """List masked intake calls for local review, newest first."""
    _require_local_review_ui(settings)
    cursor = collection.find(
        {},
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
    settings: Settings = Depends(get_settings),
    collection=Depends(get_intake_calls_collection),
) -> RedirectResponse:
    """Create a short-lived private-object URL only for a local review playback request."""
    _require_local_review_ui(settings)
    if artifact_index < 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Recording not found")
    document = await collection.find_one({"id": str(intake_call_id)}, {"intake.artifacts": 1})
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
