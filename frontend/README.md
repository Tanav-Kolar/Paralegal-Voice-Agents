# Lawyer intake portal

The portal is a Vite/React app backed by the FastAPI service. Production images include its built assets and serve the app at `/portal`. For local work, run the API on port 8000 and the Vite server on port 5173; Vite proxies `/api` to the API.

## Set up named users

1. Set `PORTAL_SESSION_SECRET` to a unique random value of at least 32 characters in `backend/.env` and in the deployed service. Keep `FIELD_ENCRYPTION_KEY` and private GCS storage configured.
   If historical calls live in other MongoDB databases, set `PORTAL_CALL_DATABASES` to their
   comma-separated, case-sensitive names. The portal account remains in `MONGODB_DATABASE`.
2. From `backend/`, run `python create_portal_user.py lawyer@example.com lawyer`. The script prompts for a display name and password (at least 12 characters). Roles are `admin`, `lawyer`, and `reviewer`. Running it again updates the named account.
3. Run `uvicorn app.main:app --reload` in `backend/`, then `bun dev` in `frontend/`. Open `http://127.0.0.1:5173` and sign in.

Portal accounts live in MongoDB. Passwords use PBKDF2-SHA256 with a per-user random salt; sessions are signed, expire after eight hours, and are sent in HttpOnly cookies. An admin or lawyer can create a new PDF brief; all three roles can review calls and save encrypted notes. The demo access code remains for the separate test-call flow, and cannot open real call details or PDF briefs.

The queue shows a caller name when available to a signed-in portal user, otherwise a masked number,
alongside a standard family-law matter category. The selected case
places a third-person triage summary above a structured Case Fact Sheet, with recording playback
and searchable transcript in the right pane. New consented calls derive the category, summary,
and facts from the recorded conversation's transcript. Older calls use any saved facts and readable
transcript; when the record does not support a summary or category, the portal says so. The portal
does not classify legal merits or infer missing facts. Manually corrected facts are marked in the
Fact Sheet and are not attributed to a transcript line.

Briefs are generated from the summary and fact sheet, saved privately in GCS as separate versions,
and downloaded through a short-lived signed URL. Each PDF retains its unverified-facts and
no-advice disclaimer. The portal does not display that disclaimer in the case workspace.

Historical stream events are grouped into expandable call sessions in the separate **Call sessions**
view. Older event records were not linked to intake call IDs; new calls save their stream ID on the
intake record. Existing encrypted transcripts require the original Fernet
`FIELD_ENCRYPTION_KEY`. A new key cannot decrypt old content. Recording playback requires an
attached private GCS artifact; consent alone does not imply that audio was saved.

## Build check

```bash
bun install --frozen-lockfile
bun run check
bun run build
```
