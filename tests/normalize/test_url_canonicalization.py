"""Tests for URL canonicalization utility behavior."""

from __future__ import annotations

from tca.normalize import canonicalize_url


def test_known_tracking_params_are_removed() -> None:
    """Known tracking query params should be stripped from canonical URL output."""
    raw_url = (
        "HTTPS://Example.COM/path?utm_source=newsletter&gclid=abc123&"
        "keep=1&utm_campaign=launch&fbclid=tracker&x=2"
    )

    result = canonicalize_url(raw_url)

    if result != "https://example.com/path?keep=1&x=2":
        raise AssertionError


def test_semantically_equivalent_urls_normalize_identically() -> None:
    """Equivalent URL variants should resolve to one canonical representation."""
    first = "https://Example.com:443/a/./b/../c?b=2&a=1#fragment"
    second = "https://example.com/a/c?a=1&b=2"

    first_result = canonicalize_url(first)
    second_result = canonicalize_url(second)

    if first_result != second_result:
        raise AssertionError
    if first_result != "https://example.com/a/c?a=1&b=2":
        raise AssertionError


def test_non_url_text_input_is_handled_safely() -> None:
    """Text that is not a URL should return None instead of raising errors."""
    if canonicalize_url("not-a-url") is not None:
        raise AssertionError
    if canonicalize_url("   ") is not None:
        raise AssertionError
    if canonicalize_url(None) is not None:
        raise AssertionError


def test_telegram_wrapped_url_is_unwrapped_and_normalized() -> None:
    """Telegram link wrappers should unwrap into the wrapped target URL."""
    wrapped = (
        "https://t.me/iv?url=https%3A%2F%2FExample.com%2Fpost%3F"
        "utm_source%3Dtelegram%26x%3D1&rhash=ignored"
    )

    result = canonicalize_url(wrapped)

    if result != "https://example.com/post?x=1":
        raise AssertionError
