"""Async SQLAlchemy engine and session wiring for SQLite storage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tca.config.settings import AppSettings

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
    return create_async_engine(
        sqlite_url,
        pool_pre_ping=True,
        future=True,
    )
