"""FastAPI application factory and lifespan management."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast, override, runtime_checkable

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse, Response

from tca.api.bearer_auth import require_bearer_auth
from tca.api.routes.channel_groups import router as channel_groups_router
from tca.api.routes.channels import router as channels_router
from tca.api.routes.dedupe_decisions import router as dedupe_decisions_router
from tca.api.routes.health import router as health_router
from tca.api.routes.jobs import router as jobs_router
from tca.api.routes.notifications import router as notifications_router
from tca.api.routes.settings import router as settings_router
from tca.api.routes.telegram_auth import router as telegram_auth_router
from tca.api.routes.thread import router as thread_router
from tca.auth import AuthStartupDependency
from tca.config.logging import init_logging
from tca.config.settings import load_settings
from tca.scheduler import SchedulerService
from tca.storage import (
    MigrationRunnerDependency,
    SettingsSeedDependency,
    StorageRuntime,
    WriterQueue,
    WriterQueueProtocol,
    create_storage_runtime,
    dispose_storage_runtime,
)
from tca.telegram import TelethonClientManager
from tca.ui import router as ui_router
from tca.ui.routes import static_files as ui_static_files

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.datastructures import Headers


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


class AllowlistCORSMiddleware(CORSMiddleware):
    """CORS middleware that emits no CORS headers for blocked preflight origins."""

    @override
    def preflight_response(self, request_headers: Headers) -> Response:
        """Reject non-allowlisted preflight requests without CORS headers."""
        origin = request_headers.get("origin")
        if origin is not None and not self.is_allowed_origin(origin=origin):
            return PlainTextResponse("Disallowed CORS origin", status_code=400)
        return super().preflight_response(request_headers)


@dataclass(slots=True)
class StartupDependencies:
    """Container for dependency lifecycle hooks managed by app lifespan."""

    db: LifecycleDependency
    settings: LifecycleDependency
    auth: LifecycleDependency
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


def _default_dependencies(app: FastAPI) -> StartupDependencies:
    """Create default startup dependency stubs for local app startup."""
    return StartupDependencies(
        db=MigrationRunnerDependency(),
        settings=SettingsSeedDependency(),
        auth=AuthStartupDependency(),
        telethon_manager=TelethonClientManager(),
        scheduler=_build_scheduler_dependency(app),
    )


def _build_scheduler_dependency(app: FastAPI) -> SchedulerService:
    """Create scheduler dependency bound to app runtime storage and queue."""

    def _runtime_provider() -> StorageRuntime:
        state_obj = cast("object", app.state)
        runtime_obj = getattr(state_obj, "storage_runtime", None)
        if not isinstance(runtime_obj, StorageRuntime):
            message = "Missing app storage runtime: app.state.storage_runtime."
            raise TypeError(message)
        return runtime_obj

    def _writer_queue_provider() -> WriterQueueProtocol:
        state_obj = cast("object", app.state)
        queue_obj = cast("object | None", getattr(state_obj, "writer_queue", None))
        submit_obj = getattr(queue_obj, "submit", None)
        if queue_obj is None or not callable(submit_obj):
            message = "Missing app writer queue: app.state.writer_queue."
            raise RuntimeError(message)
        return cast("WriterQueueProtocol", queue_obj)

    return SchedulerService(
        runtime_provider=_runtime_provider,
        writer_queue_provider=_writer_queue_provider,
    )


def _resolve_startup_dependencies(app: FastAPI) -> StartupDependencies:
    """Resolve and validate dependency hooks required for app startup."""
    raw_state = cast("object", app.state)
    raw_dependencies = getattr(raw_state, "dependencies", None)
    if raw_dependencies is None:
        raise StartupDependencyError.missing_container()

    dependency_container = cast("object", raw_dependencies)
    for name in ("db", "settings", "auth", "telethon_manager", "scheduler"):
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
    storage_runtime: StorageRuntime | None = None
    writer_queue: WriterQueueLifecycle | None = None
    startup_order: tuple[LifecycleDependency, ...] = (
        dependencies.db,
        dependencies.settings,
        dependencies.auth,
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
        storage_runtime = create_storage_runtime(settings)
        writer_queue = _build_writer_queue(app)
        app.state.storage_runtime = storage_runtime
        app.state.writer_queue = writer_queue
        for dependency in startup_order:
            await dependency.startup()
            started_dependencies.append(dependency)
        yield
    finally:
        for dependency in reversed(started_dependencies):
            await dependency.shutdown()
        if writer_queue is not None:
            await writer_queue.close()
        if storage_runtime is not None:
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
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    protected_route_dependencies = [Depends(require_bearer_auth)]
    app.state.dependencies = _default_dependencies(app)
    app.state.writer_queue_factory = WriterQueue
    _configure_cors(app=app, allow_origins=settings.cors_allow_origins)
    app.include_router(health_router)
    app.include_router(
        channels_router,
        dependencies=protected_route_dependencies,
    )
    app.include_router(
        channel_groups_router,
        dependencies=protected_route_dependencies,
    )
    app.include_router(
        settings_router,
        dependencies=protected_route_dependencies,
    )
    app.include_router(
        jobs_router,
        dependencies=protected_route_dependencies,
    )
    app.include_router(
        notifications_router,
        dependencies=protected_route_dependencies,
    )
    app.include_router(
        telegram_auth_router,
        dependencies=protected_route_dependencies,
    )
    app.include_router(
        thread_router,
        dependencies=protected_route_dependencies,
    )
    app.include_router(
        dedupe_decisions_router,
        dependencies=protected_route_dependencies,
    )
    app.include_router(
        ui_router,
        dependencies=protected_route_dependencies,
    )
    app.mount("/ui/static", ui_static_files, name="ui-static")

    app.add_api_route(
        "/openapi.json",
        endpoint=lambda: cast("dict[str, object]", app.openapi()),
        methods=["GET"],
        include_in_schema=False,
        dependencies=protected_route_dependencies,
    )

    return app


def _configure_cors(*, app: FastAPI, allow_origins: tuple[str, ...]) -> None:
    """Attach default-deny CORS policy with explicit allowlisted origins."""
    if not allow_origins:
        return

    app.add_middleware(
        AllowlistCORSMiddleware,
        allow_origins=list(allow_origins),
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=False,
    )


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
    submit_obj = getattr(queue_obj, "submit", None)
    close_obj = getattr(queue_obj, "close", None)
    if not callable(submit_obj) or not callable(close_obj):
        raise StartupWriterQueueError.invalid_queue()
    return cast("WriterQueueLifecycle", queue_obj)


def _clear_runtime_state(app: FastAPI) -> None:
    """Remove runtime objects from app state after lifespan shutdown."""
    state = cast("object", app.state)
    if hasattr(state, "storage_runtime"):
        delattr(state, "storage_runtime")
    if hasattr(state, "writer_queue"):
        delattr(state, "writer_queue")
