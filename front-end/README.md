# Local Voice Intake Review

This is a local Bun/Vite interface for reviewing masked intake calls and playing recordings stored
in the private GCS bucket. It is deliberately separate from the deployed FastAPI service.

## Start the local backend

1. In `backend/.env`, set `LOCAL_REVIEW_API_ENABLED=true`.
2. Keep `APP_ENV=development` and configure the existing Atlas and GCS credentials in `.env`.
3. Start the API:

   ```bash
   cd backend
   .venv/bin/uvicorn app.main:app --reload --no-access-log
   ```

The API remains unavailable when `APP_ENV=production`, even if the flag is set.

## Start the front-end

```bash
cd ../front-end
bun install
bun dev
```

The `cd` command above assumes the current directory is `backend/`; use `cd front-end`
from the repository root instead.

Open the local URL printed by Vite, normally `http://127.0.0.1:5173`.

The dev server proxies `/api` requests to `http://127.0.0.1:8000`. It lists calls newest first,
shows masked caller numbers, and requests a short-lived GCS playback URL only after a Play button
is clicked.

## Build check

```bash
bun run build
```
