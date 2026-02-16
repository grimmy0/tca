"""Startup unlock mode behavior for secure-interactive and auto-unlock modes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tca.config.settings import ENV_SECRET_FILE, Mode, load_settings

from .bootstrap_token import BootstrapBearerTokenDependency

if TYPE_CHECKING:
    from pathlib import Path

SENSITIVE_OPERATION_LOCKED_MESSAGE = (
    "Sensitive operations are locked. Run an unlock action first."
)


class SensitiveOperationLockedError(PermissionError):
    """Raised when a sensitive operation is attempted while key material is locked."""


class StartupUnlockModeError(RuntimeError):
    """Raised when startup unlock mode initialization fails."""

    @classmethod
    def missing_secret_file_setting(cls) -> StartupUnlockModeError:
        """Build error when auto-unlock mode has no configured secret file."""
        message = (
            "Startup unlock failed: TCA_MODE='auto-unlock' requires "
            f"{ENV_SECRET_FILE} to point at a mounted secret file."
        )
        return cls(message)

    @classmethod
    def missing_secret_file_path(cls, *, path: Path) -> StartupUnlockModeError:
        """Build error when configured auto-unlock secret file path is missing."""
        message = (
            "Startup unlock failed: configured auto-unlock secret file was not found "
            f"at '{path}'. Ensure the file is mounted and {ENV_SECRET_FILE} is set."
        )
        return cls(message)

    @classmethod
    def unreadable_secret_file(
        cls,
        *,
        path: Path,
        reason: str,
    ) -> StartupUnlockModeError:
        """Build error when configured auto-unlock secret file cannot be read."""
        message = (
            "Startup unlock failed: unable to read auto-unlock secret file "
            f"'{path}' ({reason}). Ensure the file is mounted and readable by TCA."
        )
        return cls(message)

    @classmethod
    def empty_secret_file(cls, *, path: Path) -> StartupUnlockModeError:
        """Build error when configured auto-unlock secret file is empty."""
        message = (
            "Startup unlock failed: auto-unlock secret file "
            f"'{path}' is empty. Write the secret to the file or switch "
            "TCA_MODE to 'secure-interactive'."
        )
        return cls(message)


class UnlockState:
    """Process-local unlock state used to gate sensitive operations."""

    def __init__(self) -> None:
        """Initialize locked state with secure-interactive as default mode."""
        self._mode: Mode = "secure-interactive"
        self._unlock_secret: str | None = None

    @property
    def mode(self) -> Mode:
        """Return current startup mode associated with this unlock state."""
        return self._mode

    @property
    def is_unlocked(self) -> bool:
        """Return True when sensitive operations are currently unlocked."""
        return self._unlock_secret is not None

    def lock(self, *, mode: Mode) -> None:
        """Lock sensitive operations and clear any in-memory unlock secret."""
        self._mode = mode
        self._unlock_secret = None

    def unlock_with_passphrase(self, *, passphrase: str) -> None:
        """Unlock sensitive operations from user-provided or mounted secret input."""
        if not passphrase:
            message = "Unlock passphrase cannot be empty."
            raise ValueError(message)
        self._unlock_secret = passphrase

    def require_unlocked(self) -> None:
        """Raise when sensitive operations are attempted while still locked."""
        if self._unlock_secret is None:
            raise SensitiveOperationLockedError(SENSITIVE_OPERATION_LOCKED_MESSAGE)

    def get_sensitive_operation_secret(self) -> str:
        """Return unlock secret for sensitive operations after lock check."""
        self.require_unlocked()
        secret = self._unlock_secret
        if secret is None:
            raise SensitiveOperationLockedError(SENSITIVE_OPERATION_LOCKED_MESSAGE)
        return secret


_DEFAULT_UNLOCK_STATE = UnlockState()


def initialize_startup_unlock_mode(
    *,
    mode: Mode,
    secret_file: Path | None,
    unlock_state: UnlockState | None = None,
) -> None:
    """Apply startup unlock-mode behavior and initialize unlock state."""
    state = _DEFAULT_UNLOCK_STATE if unlock_state is None else unlock_state
    state.lock(mode=mode)
    if mode == "secure-interactive":
        return
    if mode != "auto-unlock":
        message = f"Unsupported unlock mode: {mode!r}."
        raise ValueError(message)

    if secret_file is None:
        raise StartupUnlockModeError.missing_secret_file_setting()

    secret = _read_auto_unlock_secret_file(secret_file=secret_file)
    state.unlock_with_passphrase(passphrase=secret)


def get_unlock_state() -> UnlockState:
    """Return process-local unlock state singleton used by app startup."""
    return _DEFAULT_UNLOCK_STATE


def unlock_with_passphrase(
    *,
    passphrase: str,
    unlock_state: UnlockState | None = None,
) -> None:
    """Execute explicit unlock action for secure-interactive mode."""
    state = _DEFAULT_UNLOCK_STATE if unlock_state is None else unlock_state
    state.unlock_with_passphrase(passphrase=passphrase)


def require_sensitive_operation_unlock(
    *,
    unlock_state: UnlockState | None = None,
) -> None:
    """Enforce lock gate before any sensitive operation execution."""
    state = _DEFAULT_UNLOCK_STATE if unlock_state is None else unlock_state
    state.require_unlocked()


def get_sensitive_operation_secret(
    *,
    unlock_state: UnlockState | None = None,
) -> str:
    """Read unlock secret for sensitive operations after lock enforcement."""
    state = _DEFAULT_UNLOCK_STATE if unlock_state is None else unlock_state
    return state.get_sensitive_operation_secret()


@dataclass(slots=True)
class StartupUnlockDependency:
    """Lifecycle dependency that initializes unlock mode state at startup."""

    unlock_state: UnlockState = field(default_factory=get_unlock_state)

    async def startup(self) -> None:
        """Initialize unlock state based on static startup settings."""
        settings = load_settings()
        initialize_startup_unlock_mode(
            mode=settings.mode,
            secret_file=settings.secret_file,
            unlock_state=self.unlock_state,
        )

    async def shutdown(self) -> None:
        """Clear in-memory unlock state when application shuts down."""
        self.unlock_state.lock(mode=self.unlock_state.mode)


@dataclass(slots=True)
class AuthStartupDependency:
    """Composite auth dependency for unlock-mode and bootstrap-token startup hooks."""

    unlock: StartupUnlockDependency = field(default_factory=StartupUnlockDependency)
    bootstrap: BootstrapBearerTokenDependency = field(
        default_factory=BootstrapBearerTokenDependency,
    )

    async def startup(self) -> None:
        """Initialize unlock mode first, then bootstrap bearer token state."""
        await self.unlock.startup()
        try:
            await self.bootstrap.startup()
        except Exception:
            await self.unlock.shutdown()
            raise

    async def shutdown(self) -> None:
        """Shut down auth startup dependencies in reverse order."""
        await self.bootstrap.shutdown()
        await self.unlock.shutdown()


def _read_auto_unlock_secret_file(*, secret_file: Path) -> str:
    if not secret_file.exists():
        raise StartupUnlockModeError.missing_secret_file_path(path=secret_file)

    try:
        secret = secret_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise StartupUnlockModeError.unreadable_secret_file(
            path=secret_file,
            reason=str(exc),
        ) from exc

    normalized = secret.strip()
    if not normalized:
        raise StartupUnlockModeError.empty_secret_file(path=secret_file)
    return normalized
