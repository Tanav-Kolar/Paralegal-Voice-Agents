# Paralegal Voice Agents

FastAPI foundation for an AI-assisted, consent-first family-law lead-intake service for Indian law firms. The MVP supports Hindi, English, and Hinglish intake flows; it does not provide legal advice or create lawyer-client relationships.

## What is here

- FastAPI application with versioned routing, `.env` configuration, MongoDB (async) and health endpoints
- A consent-first voice intake agent: language choice, recording/intake consent, then a Sarvam-driven
  conversation. Caller transcripts are encrypted per field; audio is recorded to private storage only
  after consent.
- Two ways in, one pipeline: Exotel phone calls, and a browser demo behind an access code
- A review UI in `frontend/` for reading intakes and the browser call panel

## How a call flows

```text
Exotel WebSocket ──► ExotelTransport ─┐
                                      ├─► Session ──► Sarvam STT / chat / TTS
Browser WebSocket ─► BrowserTransport ┘       │
                                              └─► MongoDB (transcripts, events), GCS (audio)
```

- `app/voice/transport.py`: the `Transport` protocol plus the two wire adapters. Adding another
  telephony provider means writing one adapter; the session never sees provider framing.
- `app/voice/session.py`: `Session` owns the language, consent and conversation workflows, recording
  and persistence. Turns run on a worker so the agent can be interrupted mid-sentence.
- `app/api/routes/telephony.py`: Exotel webhooks, the voicebot URL resolver and stream authentication.
- `app/api/routes/demo.py`: browser demo sessions (raw PCM frames, 8 kHz mono; the text frame `stop`
  hangs up).

Barge-in is off by default (`VOICE_BARGE_IN_ENABLED`) because line echo can make the agent interrupt
itself. STT and TTS sample rates must match, since caller and agent audio share one stream.

## Local setup

The backend lives in `backend/`, the local review UI lives in `frontend/`, and
the backend tests live in the root `tests/` directory.

1. Use Python 3.11 or newer and change into the backend directory:

   ```bash
   cd backend
   ```

2. Create a virtual environment and activate it:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
3. Install the project with development dependencies:

   ```bash
   pip install -e ".[dev]"
   ```

4. Copy `.env.example` to `.env` and set `MONGODB_URI` to the MongoDB Atlas connection string for the MVP and demo.
5. Start the API:

   ```bash
   uvicorn app.main:app --reload
   ```

## Optional local MongoDB

MongoDB Atlas is the supported database for the MVP and demo. The Docker Compose service in `backend/` remains available for future local development without Atlas access. Run the following commands from `backend/`.

1. Set `MONGODB_URI` in `.env` to the local connection string from `.env.example`.
2. Start MongoDB:

   ```bash
   docker compose up -d mongodb
   ```

3. Start the API:

   ```bash
   uvicorn app.main:app --reload
   ```

The API exposes:

- `GET /api/v1/health/live` for process liveness
- `GET /api/v1/health/ready` for MongoDB readiness
- `POST /api/v1/intake-calls` for creating the initial intake-call record

Run backend checks from `backend/` with:

```bash
ruff check . ../tests
pytest
```

## CI and deployment

GitHub Actions runs backend and frontend checks for pull requests and pushes to
`main`. After checks pass on `main`, it builds the backend image and updates the
existing Cloud Run service. See [CI/CD setup](CICD.md) for authentication,
activation, and troubleshooting.
