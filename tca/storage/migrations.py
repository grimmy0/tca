"""Alembic migration runner used by application startup lifecycle hooks."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = PROJECT_ROOT / "alembic.ini"

# Prevent local 'alembic' directory from shadowing the global third-party library
_orig_sys_path = list(sys.path)
_to_remove = ["", PROJECT_ROOT.as_posix(), Path.cwd().as_posix()]
for _p in _to_remove:
    while _p in sys.path:
        sys.path.remove(_p)

try:
    from alembic import command  # pyright: ignore[reportAttributeAccessIssue, reportUnknownVariableType] # noqa: I001
    from alembic.config import Config
finally:
    sys.path = _orig_sys_path

from tca.config.settings import load_settings  # noqa: E402


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

    logger.info("Applying startup migrations to Alembic head (db=%s)", db_path)
    orig_db_path = os.environ.get("TCA_DB_PATH")
    os.environ["TCA_DB_PATH"] = db_path.as_posix()

    try:
        alembic_cfg = Config(ALEMBIC_CONFIG_PATH.as_posix())
        alembic_cfg.set_main_option(
            "script_location",
            (PROJECT_ROOT / "alembic").as_posix(),
        )
        command.upgrade(alembic_cfg, "head")  # pyright: ignore[reportUnknownMemberType]
    except Exception as exc:
        raise MigrationStartupError.for_upgrade_failure(
            db_path,
            details=str(exc),
        ) from exc
    finally:
        if orig_db_path is not None:
            os.environ["TCA_DB_PATH"] = orig_db_path
        else:
            _ = os.environ.pop("TCA_DB_PATH", None)

    logger.info("Startup migrations complete (db=%s)", db_path)


class MigrationRunnerDependency:
    """Lifecycle dependency that gates app startup on migration completion."""

    async def startup(self) -> None:
        """Run migrations before app accepts requests."""
        await asyncio.to_thread(run_startup_migrations)

    async def shutdown(self) -> None:
        """No-op shutdown hook for lifecycle protocol compatibility."""
        return
