"""URL canonicalization helpers for normalization pipelines."""

from __future__ import annotations

from posixpath import normpath
from urllib.parse import (
    SplitResult,
    parse_qsl,
    unquote,
    urlencode,
    urlsplit,
    urlunsplit,
)

_TRACKING_QUERY_KEYS = frozenset({"fbclid", "gclid"})
_TELEGRAM_WRAPPER_HOSTS = frozenset({"t.me", "telegram.me"})
_TELEGRAM_WRAPPER_PATHS = frozenset({"/iv", "/share", "/share/url"})
_DEFAULT_PORT_BY_SCHEME = {"http": 80, "https": 443}


def canonicalize_url(value: str | None) -> str | None:
    """Return canonical HTTP(S) URL or ``None`` if input is not safely URL-like."""
    if value is None:
        return None

    stripped = value.strip()
    if not stripped:
        return None

    try:
        split = urlsplit(_unwrap_telegram_url(stripped))
    except ValueError:
        return None
    scheme = split.scheme.lower()
    if not _is_supported_http_scheme(scheme):
        return None

    netloc = _canonicalize_netloc(split=split, scheme=scheme)
    if netloc is None:
        return None

    normalized_path = _canonicalize_path(split.path)
    normalized_query = _canonicalize_query(split.query)

    return urlunsplit((scheme, netloc, normalized_path, normalized_query, ""))


def _is_supported_http_scheme(scheme: str) -> bool:
    return scheme in _DEFAULT_PORT_BY_SCHEME


def _canonicalize_netloc(*, split: SplitResult, scheme: str) -> str | None:
    hostname = split.hostname
    if hostname is None:
        return None

    normalized_hostname = hostname.lower()
    try:
        port = split.port
    except ValueError:
        return None
    if port == _DEFAULT_PORT_BY_SCHEME[scheme]:
        port = None

    userinfo = ""
    if split.username is not None:
        userinfo = split.username
        if split.password is not None:
            userinfo = f"{userinfo}:{split.password}"
        userinfo = f"{userinfo}@"

    netloc = f"{userinfo}{normalized_hostname}"
    if port is not None:
        return f"{netloc}:{port}"
    return netloc


def _canonicalize_path(path: str) -> str:
    normalized_path = normpath(path or "/")
    if path.endswith("/") and normalized_path != "/":
        return f"{normalized_path}/"
    return normalized_path


def _canonicalize_query(query: str) -> str:
    kept_params = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if not _is_tracking_query_param(key)
    ]
    kept_params.sort()
    return urlencode(kept_params, doseq=True)


def _is_tracking_query_param(key: str) -> bool:
    lowered_key = key.lower()
    return lowered_key.startswith("utm_") or lowered_key in _TRACKING_QUERY_KEYS


def _unwrap_telegram_url(value: str) -> str:
    """Extract wrapped target URL from Telegram wrappers when present."""
    candidate = value
    for _ in range(2):
        split = urlsplit(candidate)
        if split.hostname not in _TELEGRAM_WRAPPER_HOSTS:
            return candidate
        if split.path not in _TELEGRAM_WRAPPER_PATHS:
            return candidate

        params = parse_qsl(split.query, keep_blank_values=True)
        target_url = next(
            (item_value for item_key, item_value in params if item_key == "url"),
            None,
        )
        if target_url is None:
            return candidate

        candidate = unquote(target_url)
    return candidate
