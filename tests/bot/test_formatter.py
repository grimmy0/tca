"""Tests for thread entry message formatter."""

from __future__ import annotations

import pytest

from tca.bot import format_delivery_message
from tca.storage.bot_deliveries_repo import BotDeliveryEntryRecord


def test_format_all_fields() -> None:
    """Ensure complete entry produces expected bold title, body, links, and badges."""
    entry = BotDeliveryEntryRecord(
        cluster_id=42,
        representative_title="Test Title",
        representative_body="This is a test body.",
        representative_canonical_url="https://example.com/canonical",
        representative_published_at=None,
        channel_name="Main Channel",
        channel_username="main_channel",
        duplicate_count=5,
    )
    result = format_delivery_message(entry)

    # Check for linked title in bold
    assert '<a href="https://example.com/canonical"><b>Test Title</b></a>' in result
    assert "This is a test body." in result
    assert "via Main Channel (@main_channel)" in result
    assert "+5 duplicates" in result


def test_format_minimal_fields() -> None:
    """Ensure all optional fields as None produces minimal valid format."""
    entry = BotDeliveryEntryRecord(
        cluster_id=42,
        representative_title=None,
        representative_body=None,
        representative_canonical_url=None,
        representative_published_at=None,
        channel_name="Main Channel",
        channel_username=None,
        duplicate_count=1,
    )
    result = format_delivery_message(entry)

    # Expected minimal format
    assert result == "via Main Channel"


def test_format_html_escaping() -> None:
    """Ensure user-generated text is properly HTML-escaped."""
    entry = BotDeliveryEntryRecord(
        cluster_id=42,
        representative_title="<script>alert(1)</script>",
        representative_body="Body & more",
        representative_canonical_url="https://example.com/a?b=1&c=2",
        representative_published_at=None,
        channel_name="<b>Name</b>",
        channel_username="bad\"user",
        duplicate_count=1,
    )
    result = format_delivery_message(entry)

    # Scrapers must escape tags
    assert "<script>" not in result
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in result
    assert "Body &amp; more" in result
    assert "https://example.com/a?b=1&amp;c=2" in result
    assert "&lt;b&gt;Name&lt;/b&gt;" in result
    assert "bad&quot;user" in result


def test_format_truncation_limits() -> None:
    """Ensure message exceeding 4096 chars is safely truncated and maintains valid HTML."""
    long_title = "x" * 5000
    entry = BotDeliveryEntryRecord(
        cluster_id=42,
        representative_title=long_title,
        representative_body="short body",
        representative_canonical_url="https://example.com/truncated",
        representative_published_at=None,
        channel_name="Test Channel",
        channel_username="test_channel",
        duplicate_count=1,
    )
    result = format_delivery_message(entry)

    # Result length must be exactly 4096 chars or less (including closing tags)
    assert len(result) <= 4096

    # Trailing indicator
    assert "…" in result

    # Check unclosed tags are closed
    # Since we have <a href="..."><b>Truncation Test</b></a>, the link and bold should close properly
    assert result.endswith("</a>")
