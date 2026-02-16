"""Tests for Argon2id KEK derivation helpers."""

from __future__ import annotations

from typing import cast
from unittest.mock import patch

from argon2.low_level import Type

from tca.auth.kdf import (
    ARGON2ID_MEMORY_COST_KIB,
    ARGON2ID_PARALLELISM,
    ARGON2ID_SALT_BYTES,
    ARGON2ID_TIME_COST,
    KEY_ENCRYPTION_KEY_BYTES,
    derive_key_encryption_key,
)

EXPECTED_MEMORY_COST_KIB = 64 * 1024
EXPECTED_TIME_COST = 3
EXPECTED_PARALLELISM = 1
EXPECTED_SALT_BYTES = 16


def test_kdf_parameters_match_design_values_exactly() -> None:
    """Ensure Argon2id call wiring matches design-baseline KDF parameters."""
    if ARGON2ID_MEMORY_COST_KIB != EXPECTED_MEMORY_COST_KIB:
        raise AssertionError
    if ARGON2ID_TIME_COST != EXPECTED_TIME_COST:
        raise AssertionError
    if ARGON2ID_PARALLELISM != EXPECTED_PARALLELISM:
        raise AssertionError
    if ARGON2ID_SALT_BYTES != EXPECTED_SALT_BYTES:
        raise AssertionError

    secret_input = "correct horse battery staple"  # noqa: S105
    salt = b"0123456789abcdef"
    expected_key = b"\x42" * KEY_ENCRYPTION_KEY_BYTES

    with patch("tca.auth.kdf.hash_secret_raw", return_value=expected_key) as mocked_kdf:
        derived_key = derive_key_encryption_key(passphrase=secret_input, salt=salt)

    if derived_key != expected_key:
        raise AssertionError
    if mocked_kdf.call_count != 1:
        raise AssertionError

    call_kwargs = cast("dict[str, object]", mocked_kdf.call_args.kwargs)
    expected_kwargs: dict[str, object] = {
        "secret": secret_input.encode("utf-8"),
        "salt": salt,
        "time_cost": ARGON2ID_TIME_COST,
        "memory_cost": ARGON2ID_MEMORY_COST_KIB,
        "parallelism": ARGON2ID_PARALLELISM,
        "hash_len": KEY_ENCRYPTION_KEY_BYTES,
        "type": Type.ID,
    }
    if call_kwargs != expected_kwargs:
        raise AssertionError


def test_same_passphrase_and_salt_yield_deterministic_key() -> None:
    """Ensure repeated derivation with identical passphrase+salt is deterministic."""
    secret_input = "deterministic-passphrase"  # noqa: S105
    salt = b"0123456789abcdef"

    first_key = derive_key_encryption_key(passphrase=secret_input, salt=salt)
    second_key = derive_key_encryption_key(passphrase=secret_input, salt=salt)

    if first_key != second_key:
        raise AssertionError
    if len(first_key) != KEY_ENCRYPTION_KEY_BYTES:
        raise AssertionError


def test_different_salt_yields_different_derived_key() -> None:
    """Ensure derivation output changes when salt changes for same passphrase."""
    secret_input = "salt-sensitive-passphrase"  # noqa: S105
    first_salt = b"0123456789abcdef"
    second_salt = b"fedcba9876543210"

    first_key = derive_key_encryption_key(passphrase=secret_input, salt=first_salt)
    second_key = derive_key_encryption_key(passphrase=secret_input, salt=second_salt)

    if first_key == second_key:
        raise AssertionError
