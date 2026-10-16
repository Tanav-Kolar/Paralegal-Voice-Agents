import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.api.routes.review_ui import ui_router
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
    app.include_router(ui_router)
    return app


app = create_app()
