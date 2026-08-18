"""Tests for export_items_to_csv helper in items_repo."""

from datetime import UTC, datetime
from tca.storage.items_repo import ItemRecord, export_items_to_csv


def test_export_items_to_csv() -> None:
    now = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)
    item = ItemRecord(
        item_id=1,
        channel_id=10,
        message_id=100,
        raw_message_id=None,
        published_at=now,
        title="Test Article Title",
        body="Body text",
        canonical_url="https://example.com/item/1",
        canonical_url_hash=None,
        content_hash=None,
        dedupe_state="unique",
        created_at=now,
        updated_at=now,
    )
    csv_out = export_items_to_csv([item])
    assert "item_id,channel_id,message_id,published_at,title,canonical_url,dedupe_state" in csv_out
    assert "1,10,100,2026-08-18T12:00:00+00:00,Test Article Title,https://example.com/item/1,unique" in csv_out
