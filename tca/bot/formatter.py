"""Formatting helper to construct Telegram HTML messages for thread entries."""

from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tca.storage.bot_deliveries_repo import BotDeliveryEntryRecord


def format_delivery_message(entry: BotDeliveryEntryRecord) -> str:
    """Format a BotDeliveryEntryRecord into a valid Telegram HTML message <= 4096 chars."""
    title = entry.representative_title
    body = entry.representative_body
    canonical_url = entry.representative_canonical_url
    channel_name = entry.channel_name
    channel_username = entry.channel_username
    duplicate_count = entry.duplicate_count

    # Escape HTML entities for user-generated content
    escaped_title = html.escape(title) if title else ""
    escaped_body = html.escape(body[:500]) if body else ""
    escaped_url = html.escape(canonical_url) if canonical_url else ""
    escaped_channel_name = html.escape(channel_name)
    escaped_channel_username = html.escape(channel_username) if channel_username else ""

    # Construct the title/link component
    title_html = ""
    if escaped_title:
        title_html = f"<b>{escaped_title}</b>"

    if escaped_url:
        if title_html:
            title_html = f'<a href="{escaped_url}">{title_html}</a>'
        else:
            title_html = f'<a href="{escaped_url}">{escaped_url}</a>'

    # Construct the channel attribution & duplicates line
    attr_parts = []
    if escaped_channel_username:
        attr_parts.append(f"via {escaped_channel_name} (@{escaped_channel_username})")
    else:
        attr_parts.append(f"via {escaped_channel_name}")

    if duplicate_count > 1:
        attr_parts.append(f"+{duplicate_count} duplicates")

    attr_html = " ".join(attr_parts)

    # Assemble parts
    parts = []
    if title_html:
        parts.append(title_html)
    if escaped_body:
        parts.append(escaped_body)
    parts.append(attr_html)

    full_message = "\n\n".join(parts)

    # Truncate to 4096 characters max while preserving HTML tag validity
    return truncate_html(full_message, max_len=4096)


def truncate_html(html_str: str, max_len: int = 4096) -> str:
    """Truncate an HTML string to max_len (inclusive) while closing open tags."""
    if len(html_str) <= max_len:
        return html_str

    token_pattern = re.compile(r"(</?[a-zA-Z0-9]+(?:\s+[^>]*)*>)")
    tokens = token_pattern.split(html_str)

    open_tags: list[str] = []
    result_parts: list[str] = []
    current_len = 0

    for token in tokens:
        if not token:
            continue

        if token.startswith("<") and token.endswith(">"):
            tag_name_match = re.match(r"^</?([a-zA-Z0-9]+)", token)
            if tag_name_match:
                tag_name = tag_name_match.group(1).lower()
                if token.startswith("</"):
                    # End tag
                    new_open_tags = list(open_tags)
                    if new_open_tags and new_open_tags[-1] == tag_name:
                        new_open_tags.pop()
                    new_closing_tags_len = sum(len(t) + 3 for t in new_open_tags)

                    if current_len + len(token) + new_closing_tags_len + 1 > max_len:
                        break

                    open_tags = new_open_tags
                    result_parts.append(token)
                    current_len += len(token)
                else:
                    # Start tag
                    new_open_tags = list(open_tags)
                    if not token.endswith("/>"):
                        new_open_tags.append(tag_name)
                    new_closing_tags_len = sum(len(t) + 3 for t in new_open_tags)

                    if current_len + len(token) + new_closing_tags_len + 1 > max_len:
                        break

                    open_tags = new_open_tags
                    result_parts.append(token)
                    current_len += len(token)
        else:
            # Text content
            closing_tags_len = sum(len(t) + 3 for t in open_tags)
            available = max_len - 1 - closing_tags_len - current_len
            if available <= 0:
                break
            if len(token) > available:
                result_parts.append(token[:available])
                current_len += available
                break
            else:
                result_parts.append(token)
                current_len += len(token)

    result_parts.append("…")
    for tag in reversed(open_tags):
        result_parts.append(f"</{tag}>")

    return "".join(result_parts)
