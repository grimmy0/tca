"""Key material helpers for deriving key-encryption keys (KEKs)."""

from __future__ import annotations

import base64
import secrets

from tca.storage import WriterQueueProtocol
from tca.storage.settings_repo import SettingAlreadyExistsError, SettingsRepository

from .kdf import ARGON2ID_SALT_BYTES, derive_key_encryption_key
from .unlock_modes import UnlockState, get_sensitive_operation_secret

AUTH_KEY_SALT_SETTING = "auth.kek_salt"


class KeyMaterialError(RuntimeError):
    """Raised when key material derivation or storage fails."""

    @classmethod
    def invalid_salt(cls, *, details: str) -> KeyMaterialError:
        """Build deterministic error for invalid stored salt values."""
        return cls(f"Stored auth key salt is invalid: {details}")

    @classmethod
    def missing_salt(cls) -> KeyMaterialError:
        """Build deterministic error when salt is missing after creation attempt."""
        return cls("Stored auth key salt is missing.")


async def resolve_key_encryption_key(
    *,
    settings_repository: SettingsRepository,
    writer_queue: WriterQueueProtocol,
    unlock_state: UnlockState | None = None,
) -> bytes:
    """Derive the current key-encryption key (KEK) from unlock secret + salt."""
    passphrase = get_sensitive_operation_secret(unlock_state=unlock_state)
    salt = await _get_or_create_kek_salt(
        settings_repository=settings_repository,
        writer_queue=writer_queue,
    )
    return derive_key_encryption_key(passphrase=passphrase, salt=salt)


async def _get_or_create_kek_salt(
    *,
    settings_repository: SettingsRepository,
    writer_queue: WriterQueueProtocol,
) -> bytes:
    existing = await settings_repository.get_by_key(key=AUTH_KEY_SALT_SETTING)
    if existing is not None:
        return _decode_salt(value=existing.value)

    new_salt = secrets.token_bytes(ARGON2ID_SALT_BYTES)
    encoded = _encode_salt(value=new_salt)

    async def _create() -> bytes | None:
        try:
            _ = await settings_repository.create(
                key=AUTH_KEY_SALT_SETTING,
                value=encoded,
            )
        except SettingAlreadyExistsError:
            return None
        return new_salt

    created = await writer_queue.submit(_create)
    if created is not None:
        return created

    existing = await settings_repository.get_by_key(key=AUTH_KEY_SALT_SETTING)
    if existing is None:
        raise KeyMaterialError.missing_salt()
    return _decode_salt(value=existing.value)


def _encode_salt(*, value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_salt(*, value: object) -> bytes:
    if not isinstance(value, str):
        raise KeyMaterialError.invalid_salt(details="expected base64-encoded string")
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError) as exc:
        raise KeyMaterialError.invalid_salt(details="invalid base64 payload") from exc
    if len(decoded) != ARGON2ID_SALT_BYTES:
        raise KeyMaterialError.invalid_salt(details="unexpected salt length")
    return decoded
