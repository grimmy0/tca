"""Argon2id passphrase derivation for key-encryption keys (KEKs)."""

from __future__ import annotations

from argon2.low_level import Type, hash_secret_raw

from .encryption_utils import DATA_ENCRYPTION_KEY_BYTES

ARGON2ID_MEMORY_COST_KIB = 64 * 1024
ARGON2ID_TIME_COST = 3
ARGON2ID_PARALLELISM = 1
ARGON2ID_SALT_BYTES = 16
KEY_ENCRYPTION_KEY_BYTES = DATA_ENCRYPTION_KEY_BYTES


def derive_key_encryption_key(*, passphrase: str, salt: bytes) -> bytes:
    """Derive an AES KEK from passphrase using Argon2id design baseline."""
    _validate_salt(salt=salt)
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=ARGON2ID_TIME_COST,
        memory_cost=ARGON2ID_MEMORY_COST_KIB,
        parallelism=ARGON2ID_PARALLELISM,
        hash_len=KEY_ENCRYPTION_KEY_BYTES,
        type=Type.ID,
    )


def _validate_salt(*, salt: bytes) -> None:
    if len(salt) != ARGON2ID_SALT_BYTES:
        message = f"salt must be exactly {ARGON2ID_SALT_BYTES} bytes."
        raise ValueError(message)
