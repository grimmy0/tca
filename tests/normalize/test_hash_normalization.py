"""Tests for hash normalization pipeline behavior."""

from __future__ import annotations

import json
from pathlib import Path

from tca.normalize import build_hash_normalized_input, normalize_hash_text

SNAPSHOT_PATH = (
    Path(__file__).resolve().parent / "snapshots" / "hash_normalization_snapshot.json"
)


def test_same_semantic_input_yields_same_normalized_hash_input() -> None:
    """Equivalent content variants should normalize to one deterministic input."""
    fullwidth_foo = "\uff26\uff4f\uff4f"
    first = build_hash_normalized_input(
        title=f"{fullwidth_foo} BAR",
        body=(
            "Read https://t.me/iv?url=https%3A%2F%2FExample.com%2Fa%2Fb%3F"
            "utm_source%3Dtelegram%26x%3D1%26gclid%3Dad&hash=ignored now!"
        ),
    )
    second = build_hash_normalized_input(
        title="foo bar",
        body="Read https://example.com/a/b?x=1 now.",
    )

    if first != second:
        raise AssertionError


def test_non_alphanumeric_collapse_behavior_matches_spec() -> None:
    """Non-alphanumeric runs should collapse into single spaces."""
    result = normalize_hash_text("Alpha---beta___gamma!!!   delta\t\nepsilon")

    if result != "alpha beta gamma delta epsilon":
        raise AssertionError


def test_snapshot_locks_hash_normalization_outputs() -> None:
    """Committed snapshot should pin deterministic outputs for representative inputs."""
    snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    cases = snapshot["cases"]

    actual = {
        "mixed_text_and_url": normalize_hash_text(
            "Launch: https://Example.com/path?utm_source=abc&x=1#frag",
        ),
        "telegram_wrapped_url": normalize_hash_text(
            "See https://t.me/share/url?url=https%3A%2F%2Fexample.com%2Fa%3F"
            "fbclid%3Dfoo%26keep%3D1",
        ),
        "build_title_body_input": build_hash_normalized_input(
            title="Title -- One",
            body="Body https://example.com:443/a/./b/../c?a=1&b=2",
        ),
        "cjk_and_fullwidth": normalize_hash_text(
            "\uff26\uff55\uff4c\uff4c\uff57\uff49\uff44\uff54\uff48"
            "\u3000\uff34\uff45\uff58\uff54 和中文",
        ),
    }

    if actual != cases:
        raise AssertionError
