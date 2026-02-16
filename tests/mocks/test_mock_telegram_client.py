"""Unit tests for MockTelegramClient behavior."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.mocks.mock_telegram_client import MockTelegramClient


@pytest.mark.asyncio
async def test_run_until_disconnected_returns_when_disconnected(
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure run loop helper does not hang when already disconnected."""
    await asyncio.wait_for(mock_tg_client.run_until_disconnected(), timeout=0.05)


@pytest.mark.asyncio
async def test_run_until_disconnected_unblocks_after_disconnect(
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure run loop helper exits promptly after disconnect."""
    await mock_tg_client.connect()
    wait_task = asyncio.create_task(mock_tg_client.run_until_disconnected())

    await asyncio.sleep(0)
    if wait_task.done():
        raise AssertionError

    await mock_tg_client.disconnect()
    await asyncio.wait_for(wait_task, timeout=0.05)
