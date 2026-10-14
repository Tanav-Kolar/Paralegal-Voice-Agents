# Paralegal Voice Agents

FastAPI foundation for an AI-assisted, consent-first family-law lead-intake service for Indian law firms. The MVP supports Hindi, English, and Hinglish intake flows; it does not provide legal advice or create lawyer-client relationships.

## Included foundation

- FastAPI application factory and versioned API routing
- Environment-based configuration via `.env`
- MongoDB async driver and lifecycle management
- Structured application logging
- Liveness and database-readiness health endpoints
- Initial intake-call schema with consent, language, and caller-number fields
- Docker Compose configuration for optional local MongoDB development

## Local setup

1. Use Python 3.11 or newer.
2. Create a virtual environment and activate it.
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

MongoDB Atlas is the supported database for the MVP and demo. The Docker Compose service remains available for future local development without Atlas access.

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

Run checks with:

```bash
ruff check .
pytest
```
