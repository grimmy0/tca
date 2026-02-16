"""Alembic migration runner used by application startup lifecycle hooks."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

from tca.config.settings import load_settings

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"
ALEMBIC_EXECUTABLE = Path(sys.executable).with_name("alembic")


class MigrationStartupError(RuntimeError):
    """Raised when startup migrations fail before API availability."""

    @classmethod
    def for_db_path_prepare_failure(
        cls,
        db_path: Path,
        *,
        details: str,
    ) -> MigrationStartupError:
        """Build error for DB path preparation failures before migration run."""
        message = (
            "Failed to prepare database path for startup migrations "
            f"(db={db_path.as_posix()}): {details}"
        )
        return cls(message)

    @classmethod
    def for_upgrade_failure(
        cls,
        db_path: Path,
        *,
        details: str,
    ) -> MigrationStartupError:
        """Build error for failed migration upgrade at process startup."""
        message = (
            "Failed to apply startup migrations with "
            f"`alembic upgrade head` (db={db_path.as_posix()}): {details}"
        )
        return cls(message)

    @classmethod
    def for_missing_executable(cls, executable: Path) -> MigrationStartupError:
        """Build error for missing Alembic CLI executable in runtime env."""
        message = f"Missing Alembic executable required at startup: {executable}."
        return cls(message)


def run_startup_migrations() -> None:
    """Upgrade database schema to Alembic head for current settings DB path."""
    settings = load_settings()
    db_path = settings.db_path.expanduser()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MigrationStartupError.for_db_path_prepare_failure(
            db_path,
            details=str(exc),
        ) from exc

    if not ALEMBIC_EXECUTABLE.exists():
        raise MigrationStartupError.for_missing_executable(ALEMBIC_EXECUTABLE)

    logger.info("Applying startup migrations to Alembic head (db=%s)", db_path)
    env = os.environ.copy()
    env["TCA_DB_PATH"] = db_path.as_posix()

    try:
        result = subprocess.run(  # noqa: S603
            [
                ALEMBIC_EXECUTABLE.as_posix(),
                "-c",
                ALEMBIC_CONFIG_PATH.as_posix(),
                "upgrade",
                "head",
            ],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    except OSError as exc:
        raise MigrationStartupError.for_upgrade_failure(
            db_path,
            details=str(exc),
        ) from exc
    if result.returncode != 0:
        output = result.stderr.strip() or result.stdout.strip() or "unknown error"
        raise MigrationStartupError.for_upgrade_failure(db_path, details=output)

    logger.info("Startup migrations complete (db=%s)", db_path)


class MigrationRunnerDependency:
    """Lifecycle dependency that gates app startup on migration completion."""

    async def startup(self) -> None:
        """Run migrations before app accepts requests."""
        await asyncio.to_thread(run_startup_migrations)

    async def shutdown(self) -> None:
        """No-op shutdown hook for lifecycle protocol compatibility."""
        return
