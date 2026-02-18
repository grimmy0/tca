"""Hash normalization helpers for content hash input generation."""

from __future__ import annotations

import re
import unicodedata

from .url_canonicalization import canonicalize_url

_URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)


def normalize_hash_text(value: str | None) -> str:
    """Normalize one text input for deterministic hash comparisons."""
    if value is None:
        return ""
    if value == "":
        return ""

    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = _normalize_embedded_urls(normalized)
    return _collapse_non_alphanumeric(normalized)


def build_hash_normalized_input(*, title: str | None, body: str | None) -> str:
    """Build normalized hash input from title and body fields."""
    return normalize_hash_text(f"{title or ''}\n{body or ''}")


def _normalize_embedded_urls(value: str) -> str:
    return _URL_PATTERN.sub(_canonicalize_match, value)


def _canonicalize_match(match: re.Match[str]) -> str:
    candidate = match.group(0)
    normalized = canonicalize_url(candidate)
    if normalized is None:
        return candidate
    return normalized


def _collapse_non_alphanumeric(value: str) -> str:
    collapsed_chars = [character if character.isalnum() else " " for character in value]
    return " ".join("".join(collapsed_chars).split())
