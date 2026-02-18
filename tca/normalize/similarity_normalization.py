"""Similarity normalization helpers for title-similarity comparisons."""

from __future__ import annotations

import re
import unicodedata

from .url_canonicalization import canonicalize_url

_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def normalize_similarity_text(value: str | None) -> str:
    """Normalize one text input for token-based similarity comparisons."""
    if value is None:
        return ""
    if value == "":
        return ""

    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = _normalize_embedded_urls(normalized)
    return _collapse_repeated_whitespace(normalized)


def build_similarity_normalized_input(*, title: str | None, body: str | None) -> str:
    """Build normalized similarity input from title and body fields."""
    return normalize_similarity_text(f"{title or ''}\n{body or ''}")


def _normalize_embedded_urls(value: str) -> str:
    return _URL_PATTERN.sub(_canonicalize_match, value)


def _canonicalize_match(match: re.Match[str]) -> str:
    candidate = match.group(0)
    normalized = canonicalize_url(candidate)
    if normalized is None:
        return candidate
    return normalized


def _collapse_repeated_whitespace(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip()
