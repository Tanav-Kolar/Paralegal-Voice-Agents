import logging

from app.core.config import Settings


def configure_logging(settings: Settings) -> None:
    """Configure predictable, structured-friendly application logs."""
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
