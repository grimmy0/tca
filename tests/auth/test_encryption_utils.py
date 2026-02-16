"""Tests for envelope encryption utility helpers."""

from __future__ import annotations

import json
import secrets
from typing import cast

import pytest

from tca.auth.encryption_utils import (
    DATA_ENCRYPTION_KEY_BYTES,
    ENVELOPE_VERSION,
    EnvelopeDecryptionError,
    decrypt_with_envelope,
    encrypt_with_envelope,
)


def test_encrypt_decrypt_round_trip_returns_exact_original_bytes() -> None:
    """Ensure envelope encrypt/decrypt returns the exact original byte payload."""
    plaintext = (
        b"\x00\x01\x02with-null-prefix\x00and-binary-suffix\xff\x10"
        b" plus utf8 bytes \xf0\x9f\x94\x90"
    )
    key_encryption_key = secrets.token_bytes(DATA_ENCRYPTION_KEY_BYTES)

    ciphertext_payload = encrypt_with_envelope(
        plaintext=plaintext,
        key_encryption_key=key_encryption_key,
    )
    decrypted = decrypt_with_envelope(
        ciphertext_payload=ciphertext_payload,
        key_encryption_key=key_encryption_key,
    )

    if decrypted != plaintext:
        raise AssertionError


def test_decrypt_with_wrong_key_fails_deterministically() -> None:
    """Ensure decrypting with the wrong KEK consistently raises one error type."""
    plaintext = b"secret-bytes-for-deterministic-wrong-key-failure"
    correct_key_encryption_key = secrets.token_bytes(DATA_ENCRYPTION_KEY_BYTES)
    wrong_key_encryption_key = secrets.token_bytes(DATA_ENCRYPTION_KEY_BYTES)
    ciphertext_payload = encrypt_with_envelope(
        plaintext=plaintext,
        key_encryption_key=correct_key_encryption_key,
    )

    for _ in range(5):
        with pytest.raises(
            EnvelopeDecryptionError,
            match="unable to decrypt payload with provided key-encryption key",
        ):
            _ = decrypt_with_envelope(
                ciphertext_payload=ciphertext_payload,
                key_encryption_key=wrong_key_encryption_key,
            )


def test_ciphertext_payload_includes_version_metadata() -> None:
    """Ensure serialized ciphertext payload includes explicit envelope version."""
    key_encryption_key = secrets.token_bytes(DATA_ENCRYPTION_KEY_BYTES)
    ciphertext_payload = encrypt_with_envelope(
        plaintext=b"version-check",
        key_encryption_key=key_encryption_key,
    )
    decoded = cast(
        "dict[str, object]",
        json.loads(ciphertext_payload.decode("utf-8")),
    )

    if "version" not in decoded:
        raise AssertionError
    if decoded["version"] != ENVELOPE_VERSION:
        raise AssertionError


def test_decrypt_rejects_boolean_version_metadata() -> None:
    """Ensure boolean version values are rejected (bool is not valid schema int)."""
    key_encryption_key = secrets.token_bytes(DATA_ENCRYPTION_KEY_BYTES)
    ciphertext_payload = encrypt_with_envelope(
        plaintext=b"strict-version-type-check",
        key_encryption_key=key_encryption_key,
    )
    decoded = cast(
        "dict[str, object]",
        json.loads(ciphertext_payload.decode("utf-8")),
    )
    decoded["version"] = True
    tampered_payload = json.dumps(
        decoded,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    with pytest.raises(
        EnvelopeDecryptionError,
        match="unable to decrypt payload with provided key-encryption key",
    ):
        _ = decrypt_with_envelope(
            ciphertext_payload=tampered_payload,
            key_encryption_key=key_encryption_key,
        )


def test_decrypt_rejects_invalid_nonce_length_with_deterministic_error() -> None:
    """Ensure malformed nonce length is normalized to EnvelopeDecryptionError."""
    key_encryption_key = secrets.token_bytes(DATA_ENCRYPTION_KEY_BYTES)
    ciphertext_payload = encrypt_with_envelope(
        plaintext=b"nonce-length-validation",
        key_encryption_key=key_encryption_key,
    )
    decoded = cast(
        "dict[str, object]",
        json.loads(ciphertext_payload.decode("utf-8")),
    )
    decoded["nonce"] = ""
    tampered_payload = json.dumps(
        decoded,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    with pytest.raises(
        EnvelopeDecryptionError,
        match="unable to decrypt payload with provided key-encryption key",
    ):
        _ = decrypt_with_envelope(
            ciphertext_payload=tampered_payload,
            key_encryption_key=key_encryption_key,
        )
