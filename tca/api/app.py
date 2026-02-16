"""FastAPI application factory and lifespan management."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI

from tca.config.logging import init_logging
from tca.config.settings import load_settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown events."""
    _ = app
    settings = load_settings()

    logger.info(
        "Starting TCA in %s mode (bind=%s, db=%s)",
        settings.mode,
        settings.bind,
        settings.db_path,
    )

    yield

    logger.info("Shutting down TCA")


def create_app() -> FastAPI:
    """Create and configure a new FastAPI application instance."""
    settings = load_settings()
    init_logging(settings.log_level)

    return FastAPI(
        title="TCA",
        description="Threaded Channel Aggregator",
        version="0.1.0",
        lifespan=lifespan,
    )
