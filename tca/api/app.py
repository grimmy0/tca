"""FastAPI application factory and lifespan management."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from fastapi import FastAPI

from tca.api.routes.health import router as health_router
from tca.api.routes.settings import router as settings_router
from tca.config.logging import init_logging
from tca.config.settings import load_settings
from tca.storage import (
    MigrationRunnerDependency,
    SettingsSeedDependency,
    WriterQueue,
    WriterQueueProtocol,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class StartupWriterQueueError(RuntimeError):
    """Raised when app writer queue setup is missing required hooks."""

    @classmethod
    def invalid_factory(cls) -> StartupWriterQueueError:
        """Build deterministic error for invalid writer queue factory objects."""
        message = "Invalid writer queue factory: expected callable on app.state."
        return cls(message)

    @classmethod
    def invalid_queue(cls) -> StartupWriterQueueError:
        """Build deterministic error for invalid writer queue runtime objects."""
        message = "Invalid writer queue: expected submit(...) and close() methods."
        return cls(message)


logger = logging.getLogger(__name__)


class StartupDependencyError(RuntimeError):
    """Raised when required startup dependencies are missing."""

    @classmethod
    def missing_container(cls) -> StartupDependencyError:
        """Build error for absent dependency container on app state."""
        message = "Missing startup dependency container: app.state.dependencies."
        return cls(message)

    @classmethod
    def missing_named_dependency(cls, name: str) -> StartupDependencyError:
        """Build error for absent named dependency in the container."""
        message = f"Missing startup dependency: {name}."
        return cls(message)


class StartupDependencyTypeError(TypeError):
    """Raised when a dependency lacks startup/shutdown lifecycle hooks."""

    @classmethod
    def invalid_dependency(cls, name: str) -> StartupDependencyTypeError:
        """Build error for dependency objects with wrong runtime type."""
        message = (
            f"Invalid startup dependency '{name}': expected startup/shutdown hooks."
        )
        return cls(message)


@runtime_checkable
class LifecycleDependency(Protocol):
    """Protocol for startup/shutdown-managed app dependencies."""

    async def startup(self) -> None:
        """Run dependency startup actions."""

    async def shutdown(self) -> None:
        """Run dependency shutdown actions."""


class WriterQueueLifecycle(WriterQueueProtocol, Protocol):
    """Protocol for app-scoped writer queue lifecycle behavior."""

    async def close(self) -> None:
        """Stop queue worker and drain outstanding write jobs."""


@dataclass(slots=True)
class StartupDependencies:
    """Container for dependency lifecycle hooks managed by app lifespan."""

    db: LifecycleDependency
    settings: LifecycleDependency
    telethon_manager: LifecycleDependency
    scheduler: LifecycleDependency


@dataclass(slots=True)
class NoopDependency:
    """No-op lifecycle dependency used as phase-0 startup stub."""

    name: str

    async def startup(self) -> None:
        """No-op startup hook placeholder."""
        logger.debug("Startup stub executed for %s", self.name)

    async def shutdown(self) -> None:
        """No-op shutdown hook placeholder."""
        logger.debug("Shutdown stub executed for %s", self.name)


def _default_dependencies() -> StartupDependencies:
    """Create default startup dependency stubs for local app startup."""
    return StartupDependencies(
        db=MigrationRunnerDependency(),
        settings=SettingsSeedDependency(),
        telethon_manager=NoopDependency("telethon_manager"),
        scheduler=NoopDependency("scheduler"),
    )


def _resolve_startup_dependencies(app: FastAPI) -> StartupDependencies:
    """Resolve and validate dependency hooks required for app startup."""
    raw_state = cast("object", app.state)
    raw_dependencies = getattr(raw_state, "dependencies", None)
    if raw_dependencies is None:
        raise StartupDependencyError.missing_container()

    dependency_container = cast("object", raw_dependencies)
    for name in ("db", "settings", "telethon_manager", "scheduler"):
        dependency = getattr(dependency_container, name, None)
        if dependency is None:
            raise StartupDependencyError.missing_named_dependency(name)
        if not isinstance(dependency, LifecycleDependency):
            raise StartupDependencyTypeError.invalid_dependency(name)

    return cast("StartupDependencies", raw_dependencies)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manage application startup and shutdown events."""
    dependencies = _resolve_startup_dependencies(app)
    settings = load_settings()
    storage_runtime = create_storage_runtime(settings)
    writer_queue = _build_writer_queue(app)
    app.state.storage_runtime = storage_runtime
    app.state.writer_queue = writer_queue
    startup_order: tuple[LifecycleDependency, ...] = (
        dependencies.db,
        dependencies.settings,
        dependencies.telethon_manager,
        dependencies.scheduler,
    )
    started_dependencies: list[LifecycleDependency] = []

    logger.info(
        "Starting TCA in %s mode (bind=%s, db=%s)",
        settings.mode,
        settings.bind,
        settings.db_path,
    )
    try:
        for dependency in startup_order:
            await dependency.startup()
            started_dependencies.append(dependency)
        yield
    finally:
        for dependency in reversed(started_dependencies):
            await dependency.shutdown()
        await writer_queue.close()
        await dispose_storage_runtime(storage_runtime)
        _clear_runtime_state(app)
        logger.info("Shutting down TCA")


def create_app() -> FastAPI:
    """Create and configure a new FastAPI application instance."""
    settings = load_settings()
    init_logging(settings.log_level)

    app = FastAPI(
        title="TCA",
        description="Threaded Channel Aggregator",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.dependencies = _default_dependencies()
    app.state.writer_queue_factory = WriterQueue
    app.include_router(health_router)
    app.include_router(settings_router)

    return app


def _build_writer_queue(app: FastAPI) -> WriterQueueLifecycle:
    """Construct writer queue from app-state factory with runtime validation."""
    factory_obj = getattr(
        cast("object", app.state),
        "writer_queue_factory",
        WriterQueue,
    )
    if not callable(factory_obj):
        raise StartupWriterQueueError.invalid_factory()

    queue_obj = cast("object", factory_obj())
    if not hasattr(queue_obj, "submit") or not hasattr(queue_obj, "close"):
        raise StartupWriterQueueError.invalid_queue()
    return cast("WriterQueueLifecycle", queue_obj)


def _clear_runtime_state(app: FastAPI) -> None:
    """Remove runtime objects from app state after lifespan shutdown."""
    state = cast("object", app.state)
    if hasattr(state, "storage_runtime"):
        delattr(state, "storage_runtime")
    if hasattr(state, "writer_queue"):
        delattr(state, "writer_queue")
