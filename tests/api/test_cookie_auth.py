"""Unit tests for cookie signing and verification functions."""

from __future__ import annotations

import time

from tca.api.cookie_auth import (
    SIGNING_KEY_BYTES,
    create_signed_cookie_value,
    generate_cookie_signing_key,
    verify_signed_cookie_value,
)


def test_create_and_verify_roundtrip() -> None:
    """Signed cookie value should verify successfully with the same key."""
    key = generate_cookie_signing_key()
    issued_at = int(time.time())
    value = create_signed_cookie_value(signing_key=key, issued_at=issued_at)

    result = verify_signed_cookie_value(
        signing_key=key,
        cookie_value=value,
        max_age_seconds=3600,
    )
    if not result:
        raise AssertionError


def test_verify_rejects_tampered_timestamp() -> None:
    """Altered timestamp should fail HMAC verification."""
    key = generate_cookie_signing_key()
    issued_at = int(time.time())
    value = create_signed_cookie_value(signing_key=key, issued_at=issued_at)

    parts = value.split(".", maxsplit=1)
    tampered = f"{int(parts[0]) + 1}.{parts[1]}"

    result = verify_signed_cookie_value(
        signing_key=key,
        cookie_value=tampered,
        max_age_seconds=3600,
    )
    if result:
        raise AssertionError


def test_verify_rejects_tampered_signature() -> None:
    """Altered HMAC signature should fail verification."""
    key = generate_cookie_signing_key()
    issued_at = int(time.time())
    value = create_signed_cookie_value(signing_key=key, issued_at=issued_at)

    parts = value.split(".", maxsplit=1)
    tampered = f"{parts[0]}.{'0' * 64}"

    result = verify_signed_cookie_value(
        signing_key=key,
        cookie_value=tampered,
        max_age_seconds=3600,
    )
    if result:
        raise AssertionError


def test_verify_rejects_expired_cookie() -> None:
    """Cookie issued beyond max_age should fail freshness check."""
    key = generate_cookie_signing_key()
    old_timestamp = int(time.time()) - 7200
    value = create_signed_cookie_value(signing_key=key, issued_at=old_timestamp)

    result = verify_signed_cookie_value(
        signing_key=key,
        cookie_value=value,
        max_age_seconds=3600,
    )
    if result:
        raise AssertionError


def test_verify_rejects_malformed_value() -> None:
    """Malformed cookie values without proper format should fail."""
    key = generate_cookie_signing_key()

    for bad_value in ("", "nodot", "not_a_number.abcdef", "..."):
        result = verify_signed_cookie_value(
            signing_key=key,
            cookie_value=bad_value,
            max_age_seconds=3600,
        )
        if result:
            msg = f"Expected rejection for: {bad_value!r}"
            raise AssertionError(msg)


def test_verify_rejects_wrong_signing_key() -> None:
    """Cookie signed with key A should not verify with key B."""
    key_a = generate_cookie_signing_key()
    key_b = generate_cookie_signing_key()
    issued_at = int(time.time())
    value = create_signed_cookie_value(signing_key=key_a, issued_at=issued_at)

    result = verify_signed_cookie_value(
        signing_key=key_b,
        cookie_value=value,
        max_age_seconds=3600,
    )
    if result:
        raise AssertionError


def test_generate_key_length() -> None:
    """Generated signing key should be exactly SIGNING_KEY_BYTES long."""
    key = generate_cookie_signing_key()
    if len(key) != SIGNING_KEY_BYTES:
        raise AssertionError
