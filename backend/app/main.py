import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.mongodb import MongoDB

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings)
    app.state.mongodb = MongoDB(settings)
    logger.info("application_started environment=%s", settings.app_env)
    yield
    await app.state.mongodb.close()
    logger.info("application_stopped")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(api_router, prefix=settings.api_v1_prefix)
    frontend_dist = Path(__file__).resolve().parents[1] / "frontend_dist"
    if frontend_dist.is_dir():
        app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="portal-assets")

        @app.get("/", include_in_schema=False)
        @app.get("/portal", include_in_schema=False)
        async def portal_frontend() -> FileResponse:
            return FileResponse(frontend_dist / "index.html")
    return app


app = create_app()
