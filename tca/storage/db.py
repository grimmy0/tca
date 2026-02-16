"""Async SQLAlchemy engine and session wiring for SQLite storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Protocol

    from tca.config.settings import AppSettings

    class _DBAPICursor(Protocol):
        def execute(self, statement: str) -> object: ...

        def close(self) -> None: ...

    class _DBAPIConnection(Protocol):
        def cursor(self) -> _DBAPICursor: ...


SQLITE_PRAGMA_STATEMENTS: tuple[str, ...] = (
    "PRAGMA journal_mode=WAL;",
    "PRAGMA synchronous=NORMAL;",
    "PRAGMA foreign_keys=ON;",
    "PRAGMA busy_timeout=5000;",
)

SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(slots=True)
class StorageRuntime:
    """Bundled read/write engines and session factories."""

    read_engine: AsyncEngine
    write_engine: AsyncEngine
    read_session_factory: SessionFactory
    write_session_factory: SessionFactory


def build_sqlite_url(db_path: Path) -> str:
    """Build SQLAlchemy async SQLite URL from configured db path."""
    normalized_path = db_path.expanduser()
    return f"sqlite+aiosqlite:///{normalized_path.as_posix()}"


def create_read_engine(settings: AppSettings) -> AsyncEngine:
    """Create async engine for read-only query sessions."""
    return _create_engine(settings)


def create_write_engine(settings: AppSettings) -> AsyncEngine:
    """Create async engine for write-path query sessions."""
    return _create_engine(settings)


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Create typed async session factory for the supplied engine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


def create_storage_runtime(settings: AppSettings) -> StorageRuntime:
    """Create read/write engines with corresponding session factories."""
    read_engine = create_read_engine(settings)
    write_engine = create_write_engine(settings)
    return StorageRuntime(
        read_engine=read_engine,
        write_engine=write_engine,
        read_session_factory=create_session_factory(read_engine),
        write_session_factory=create_session_factory(write_engine),
    )


async def dispose_storage_runtime(runtime: StorageRuntime) -> None:
    """Dispose read/write engines for fixture teardown and app shutdown."""
    await runtime.read_engine.dispose()
    await runtime.write_engine.dispose()


def _create_engine(settings: AppSettings) -> AsyncEngine:
    """Create async SQLite engine bound to configured DB path."""
    sqlite_url = build_sqlite_url(settings.db_path)
    engine = create_async_engine(
        sqlite_url,
        pool_pre_ping=True,
        future=True,
    )
    _install_sqlite_pragma_handler(engine)
    return engine


def _install_sqlite_pragma_handler(engine: AsyncEngine) -> None:
    """Apply mandatory SQLite PRAGMAs on each fresh connection."""

    def _set_sqlite_pragmas(
        dbapi_connection: object,
        connection_record: object,
    ) -> None:
        _ = connection_record
        connection = cast("_DBAPIConnection", dbapi_connection)
        cursor = connection.cursor()
        try:
            for statement in SQLITE_PRAGMA_STATEMENTS:
                _ = cursor.execute(statement)
        finally:
            cursor.close()

    event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
