"""Bootstrap bearer token generation and one-time output handling."""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tca.config.settings import load_settings
from tca.storage.db import create_storage_runtime, dispose_storage_runtime
from tca.storage.settings_repo import SettingAlreadyExistsError, SettingsRepository

if TYPE_CHECKING:
    from collections.abc import Mapping

BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY = "auth.bootstrap_bearer_token_sha256"  # noqa: S105
ENV_BOOTSTRAP_TOKEN_OUTPUT_PATH = "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH"  # noqa: S105
DEFAULT_BOOTSTRAP_TOKEN_OUTPUT_PATH = Path("/data/bootstrap-bearer-token.txt")

logger = logging.getLogger(__name__)


def resolve_bootstrap_token_output_path(
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve one-time bootstrap token output path from environment."""
    env = os.environ if environ is None else environ
    raw_path = env.get(ENV_BOOTSTRAP_TOKEN_OUTPUT_PATH)
    if raw_path is None:
        return DEFAULT_BOOTSTRAP_TOKEN_OUTPUT_PATH

    normalized = raw_path.strip()
    if not normalized:
        return DEFAULT_BOOTSTRAP_TOKEN_OUTPUT_PATH
    return Path(normalized).expanduser()


def compute_token_sha256_digest(*, token: str) -> str:
    """Compute hex SHA-256 digest for bearer token verification storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def ensure_bootstrap_bearer_token(
    *,
    repository: SettingsRepository,
    output_path: Path,
) -> bool:
    """Create bootstrap token once and persist only its digest."""
    existing = await repository.get_by_key(key=BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY)
    if existing is not None:
        return False

    plain_token = secrets.token_urlsafe(32)
    digest = compute_token_sha256_digest(token=plain_token)
    try:
        _ = await repository.create(
            key=BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY,
            value=digest,
        )
    except SettingAlreadyExistsError:
        return False

    try:
        _write_bootstrap_token(output_path=output_path, token=plain_token)
    except OSError:
        rolled_back = await repository.delete_if_value_matches(
            key=BOOTSTRAP_BEARER_TOKEN_DIGEST_KEY,
            value=digest,
        )
        if not rolled_back:
            logger.exception(
                (
                    "Failed to roll back bootstrap bearer token digest after "
                    "output write failure (path=%s)"
                ),
                output_path,
            )
        raise
    logger.info(
        "Generated bootstrap bearer token and wrote one-time output to %s",
        output_path,
    )
    return True


@dataclass(slots=True)
class BootstrapBearerTokenDependency:
    """Lifecycle dependency ensuring bootstrap bearer token is initialized once."""

    async def startup(self) -> None:
        """Ensure bootstrap token digest exists before API starts serving traffic."""
        settings = load_settings()
        runtime = create_storage_runtime(settings)
        repository = SettingsRepository(
            read_session_factory=runtime.read_session_factory,
            write_session_factory=runtime.write_session_factory,
        )
        output_path = resolve_bootstrap_token_output_path()

        try:
            _ = await ensure_bootstrap_bearer_token(
                repository=repository,
                output_path=output_path,
            )
        finally:
            await dispose_storage_runtime(runtime)

    async def shutdown(self) -> None:
        """No-op lifecycle hook for startup-only bootstrap behavior."""
        return


def _write_bootstrap_token(*, output_path: Path, token: str) -> None:
    """Write bootstrap token as single-line output for first-run retrieval."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _ = output_path.write_text(f"{token}\n", encoding="utf-8")
    output_path.chmod(0o600)
