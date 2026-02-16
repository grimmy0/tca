"""Envelope encryption helpers for local secret-at-rest protection."""

from __future__ import annotations

import base64
import binascii
import json
import secrets
from typing import cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import (
    InvalidUnwrap,
    aes_key_unwrap,
    aes_key_wrap,
)

ENVELOPE_VERSION = 1
DATA_ENCRYPTION_KEY_BYTES = 32
AES_GCM_NONCE_BYTES = 12

_AES_KEY_SIZES_BYTES = frozenset({16, 24, 32})
_DECRYPTION_ERROR_MESSAGE = "unable to decrypt payload with provided key-encryption key"


class EnvelopeDecryptionError(ValueError):
    """Raised when envelope payload decryption cannot be completed."""


def generate_data_encryption_key() -> bytes:
    """Generate a random 256-bit data-encryption key (DEK)."""
    return secrets.token_bytes(DATA_ENCRYPTION_KEY_BYTES)


def wrap_data_encryption_key(
    *,
    data_encryption_key: bytes,
    key_encryption_key: bytes,
) -> bytes:
    """Wrap a DEK with the provided KEK using AES key wrap (RFC 3394)."""
    _validate_dek(data_encryption_key=data_encryption_key)
    _validate_aes_key(name="key_encryption_key", key=key_encryption_key)
    return aes_key_wrap(key_encryption_key, data_encryption_key)


def unwrap_data_encryption_key(
    *,
    wrapped_data_encryption_key: bytes,
    key_encryption_key: bytes,
) -> bytes:
    """Unwrap a DEK with the provided KEK using AES key wrap (RFC 3394)."""
    _validate_aes_key(name="key_encryption_key", key=key_encryption_key)
    try:
        unwrapped = aes_key_unwrap(key_encryption_key, wrapped_data_encryption_key)
    except InvalidUnwrap as exc:
        raise EnvelopeDecryptionError(_DECRYPTION_ERROR_MESSAGE) from exc

    if len(unwrapped) != DATA_ENCRYPTION_KEY_BYTES:
        raise EnvelopeDecryptionError(_DECRYPTION_ERROR_MESSAGE)
    return unwrapped


def encrypt_with_envelope(
    *,
    plaintext: bytes,
    key_encryption_key: bytes,
) -> bytes:
    """Encrypt bytes using a random DEK wrapped by the provided KEK."""
    _validate_aes_key(name="key_encryption_key", key=key_encryption_key)
    data_encryption_key = generate_data_encryption_key()
    wrapped_dek = wrap_data_encryption_key(
        data_encryption_key=data_encryption_key,
        key_encryption_key=key_encryption_key,
    )
    nonce = secrets.token_bytes(AES_GCM_NONCE_BYTES)
    ciphertext = AESGCM(data_encryption_key).encrypt(nonce, plaintext, None)

    payload = {
        "version": ENVELOPE_VERSION,
        "wrapped_data_encryption_key": _encode_bytes(wrapped_dek),
        "nonce": _encode_bytes(nonce),
        "ciphertext": _encode_bytes(ciphertext),
    }
    serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return serialized.encode("utf-8")


def decrypt_with_envelope(
    *,
    ciphertext_payload: bytes,
    key_encryption_key: bytes,
) -> bytes:
    """Decrypt envelope payload using KEK for DEK unwrapping + AES-GCM decrypt."""
    _validate_aes_key(name="key_encryption_key", key=key_encryption_key)
    payload = _decode_payload(ciphertext_payload=ciphertext_payload)

    version_obj = payload.get("version")
    if (
        not isinstance(version_obj, int)
        or isinstance(version_obj, bool)
        or version_obj != ENVELOPE_VERSION
    ):
        raise EnvelopeDecryptionError(_DECRYPTION_ERROR_MESSAGE)

    wrapped_dek = _decode_bytes(
        encoded=payload.get("wrapped_data_encryption_key"),
    )
    nonce = _decode_bytes(encoded=payload.get("nonce"))
    ciphertext = _decode_bytes(encoded=payload.get("ciphertext"))
    if len(nonce) != AES_GCM_NONCE_BYTES:
        raise EnvelopeDecryptionError(_DECRYPTION_ERROR_MESSAGE)

    data_encryption_key = unwrap_data_encryption_key(
        wrapped_data_encryption_key=wrapped_dek,
        key_encryption_key=key_encryption_key,
    )

    try:
        return AESGCM(data_encryption_key).decrypt(nonce, ciphertext, None)
    except (InvalidTag, ValueError) as exc:
        raise EnvelopeDecryptionError(_DECRYPTION_ERROR_MESSAGE) from exc


def _validate_aes_key(*, name: str, key: bytes) -> None:
    if len(key) not in _AES_KEY_SIZES_BYTES:
        valid_sizes = ", ".join(str(size) for size in sorted(_AES_KEY_SIZES_BYTES))
        message = f"{name} must be an AES key length ({valid_sizes} bytes)."
        raise ValueError(message)


def _validate_dek(*, data_encryption_key: bytes) -> None:
    if len(data_encryption_key) != DATA_ENCRYPTION_KEY_BYTES:
        message = (
            f"data_encryption_key must be exactly {DATA_ENCRYPTION_KEY_BYTES} bytes."
        )
        raise ValueError(message)


def _encode_bytes(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode_bytes(*, encoded: object) -> bytes:
    if not isinstance(encoded, str):
        raise EnvelopeDecryptionError(_DECRYPTION_ERROR_MESSAGE)
    try:
        return base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise EnvelopeDecryptionError(_DECRYPTION_ERROR_MESSAGE) from exc


def _decode_payload(*, ciphertext_payload: bytes) -> dict[str, object]:
    try:
        decoded_obj = cast("object", json.loads(ciphertext_payload.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvelopeDecryptionError(_DECRYPTION_ERROR_MESSAGE) from exc

    if not isinstance(decoded_obj, dict):
        raise EnvelopeDecryptionError(_DECRYPTION_ERROR_MESSAGE)
    return cast("dict[str, object]", decoded_obj)
