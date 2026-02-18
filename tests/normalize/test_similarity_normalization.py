"""Tests for similarity normalization pipeline behavior."""

from __future__ import annotations

import json
from pathlib import Path

from tca.normalize import (
    build_similarity_normalized_input,
    normalize_hash_text,
    normalize_similarity_text,
)

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent
    / "snapshots"
    / "similarity_normalization_snapshot.json"
)


def test_whitespace_boundaries_are_preserved_for_tokenization() -> None:
    """Token boundaries should be driven by whitespace, not punctuation stripping."""
    result = normalize_similarity_text("Alpha---beta___gamma!!!   delta\t\nepsilon")

    if result != "alpha---beta___gamma!!! delta epsilon":
        raise AssertionError
    if result.split() != ["alpha---beta___gamma!!!", "delta", "epsilon"]:
        raise AssertionError


def test_tracking_params_and_wrappers_are_removed() -> None:
    """Embedded wrapped URLs should be canonicalized for similarity inputs."""
    result = build_similarity_normalized_input(
        title="Read",
        body=(
            "https://t.me/share/url?url=https%3A%2F%2FExample.com%2Fa%2Fb%3F"
            "utm_source%3Dtelegram%26x%3D1%26gclid%3Dad&hash=ignored now!"
        ),
    )

    if result != "read https://example.com/a/b?x=1 now!":
        raise AssertionError


def test_snapshot_locks_similarity_and_hash_divergence() -> None:
    """Snapshot should pin expected divergence between hash and similarity pipelines."""
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    cases = snapshot["cases"]

    inputs = {
        "punctuation_boundaries_preserved": "Alpha---beta___gamma!!!   delta",
        "url_wrappers_and_tracking_removed": (
            "Launch https://t.me/iv?url=https%3A%2F%2FExample.com%2Fa%2Fb%3F"
            "utm_source%3Dx%26gclid%3Dy%26keep%3D1&rhash=ignored now!"
        ),
        "already_whitespace_tokenized": "foo bar baz",
    }
    actual = {
        key: {
            "input": value,
            "similarity": normalize_similarity_text(value),
            "hash": normalize_hash_text(value),
            "diverges_from_hash": normalize_similarity_text(value)
            != normalize_hash_text(value),
        }
        for key, value in inputs.items()
    }

    if actual != cases:
        raise AssertionError

    if not cases["punctuation_boundaries_preserved"]["diverges_from_hash"]:
        raise AssertionError
    if not cases["url_wrappers_and_tracking_removed"]["diverges_from_hash"]:
        raise AssertionError
    if cases["already_whitespace_tokenized"]["diverges_from_hash"]:
        raise AssertionError
