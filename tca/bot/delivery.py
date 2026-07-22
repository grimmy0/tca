"""Core loop and lifecycle service for Telegram Bot feed delivery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from tca.bot import BotApiClient, format_delivery_message
from tca.storage import (
    BotDeliveriesRepository,
    BotDeliveryRecord,
    SettingsRepository,
    StorageRuntime,
    WriterQueueProtocol,
)

if TYPE_CHECKING:
    from tca.storage.bot_deliveries_repo import BotDeliveryEntryRecord

logger = logging.getLogger(__name__)

RuntimeProvider = Callable[[], StorageRuntime]
WriterQueueProvider = Callable[[], WriterQueueProtocol]


class BotDeliveryCoreLoop:
    """Read undelivered timeline clusters, format them, and send via Telegram."""

    _settings_repo: SettingsRepository
    _bot_deliveries_repo: BotDeliveriesRepository
    _bot_api_client: BotApiClient
    _writer_queue: WriterQueueProtocol | None
    _formatter: Callable[[BotDeliveryEntryRecord], str]

    def __init__(
        self,
        *,
        settings_repo: SettingsRepository,
        bot_deliveries_repo: BotDeliveriesRepository,
        bot_api_client: BotApiClient,
        writer_queue: WriterQueueProtocol | None = None,
        formatter: Callable[[BotDeliveryEntryRecord], str] = format_delivery_message,
    ) -> None:
        """Create core loop with repository dependencies."""
        self._settings_repo = settings_repo
        self._bot_deliveries_repo = bot_deliveries_repo
        self._bot_api_client = bot_api_client
        self._writer_queue = writer_queue
        self._formatter = formatter

    async def run_once(self) -> list[BotDeliveryRecord]:
        """Perform one delivery run, sending a batch of undelivered clusters."""
        # 1. Check if bot is enabled
        enabled_rec = await self._settings_repo.get_by_key(key="bot.enabled")
        enabled = bool(enabled_rec.value) if enabled_rec else False
        if not enabled:
            return []

        # 2. Check token and chat_id
        token_rec = await self._settings_repo.get_by_key(key="bot.token")
        chat_id_rec = await self._settings_repo.get_by_key(key="bot.chat_id")
        token = str(token_rec.value) if token_rec else None
        chat_id = str(chat_id_rec.value) if chat_id_rec else None

        if not token or not chat_id:
            return []

        # 3. Retrieve batch size
        batch_size_rec = await self._settings_repo.get_by_key(
            key="bot.delivery_batch_size",
        )
        batch_size = int(batch_size_rec.value) if batch_size_rec else 10

        # 4. Fetch undelivered items
        undelivered = await self._bot_deliveries_repo.list_undelivered_entries(
            limit=batch_size,
        )
        delivered_records: list[BotDeliveryRecord] = []

        # 5. Process each entry
        for entry in undelivered:
            try:
                formatted = self._formatter(entry)
                sent = await self._bot_api_client.send_message(
                    token=token,
                    chat_id=chat_id,
                    text=formatted,
                )

                async def _write_delivery() -> BotDeliveryRecord:
                    return await self._bot_deliveries_repo.record_delivery(
                        cluster_id=entry.cluster_id,
                        telegram_message_id=str(sent.message_id),
                    )

                if self._writer_queue is not None:
                    rec = await self._writer_queue.submit(_write_delivery)
                else:
                    rec = await _write_delivery()
                delivered_records.append(rec)
            except Exception as exc:
                logger.warning(
                    "Failed to deliver cluster %d: %s",
                    entry.cluster_id,
                    exc,
                )
                continue

        return delivered_records


class BotDeliveryService:
    """Lifecycle dependency managing the background loop for bot deliveries."""

    runtime_provider: RuntimeProvider
    writer_queue_provider: WriterQueueProvider | None
    delivery_interval_seconds: int
    tick_interval_seconds: float
    _task: asyncio.Task[None] | None
    _stop_event: asyncio.Event | None

    def __init__(
        self,
        *,
        runtime_provider: RuntimeProvider,
        writer_queue_provider: WriterQueueProvider | None = None,
        delivery_interval_seconds: int = 60,
        tick_interval_seconds: float = 1.0,
    ) -> None:
        """Create background service with required state provider callbacks."""
        self.runtime_provider = runtime_provider
        self.writer_queue_provider = writer_queue_provider
        self.delivery_interval_seconds = delivery_interval_seconds
        self.tick_interval_seconds = tick_interval_seconds
        self._task = None
        self._stop_event = None

    async def startup(self) -> None:
        """Start the background delivery service runner task."""
        if self.is_running:
            return

        runtime = self.runtime_provider()
        settings_repo = SettingsRepository(
            read_session_factory=runtime.read_session_factory,
            write_session_factory=runtime.write_session_factory,
        )

        # Resolve dynamic interval settings
        interval_rec = await settings_repo.get_by_key(
            key="bot.delivery_interval_seconds",
        )
        if interval_rec and isinstance(interval_rec.value, int) and interval_rec.value > 0:
            self.delivery_interval_seconds = interval_rec.value

        writer_queue = (
            self.writer_queue_provider()
            if self.writer_queue_provider is not None
            else None
        )

        core_loop = BotDeliveryCoreLoop(
            settings_repo=settings_repo,
            bot_deliveries_repo=BotDeliveriesRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            bot_api_client=BotApiClient(),
            writer_queue=writer_queue,
        )

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(core_loop))

    async def shutdown(self) -> None:
        """Signal background task shutdown and await completion."""
        if self._task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        await self._task
        self._task = None
        self._stop_event = None

    @property
    def is_running(self) -> bool:
        """Return True if background delivery loop runner is active."""
        return self._task is not None and not self._task.done()

    async def _run_loop(self, core_loop: BotDeliveryCoreLoop) -> None:
        stop_event = self._stop_event
        if stop_event is None:
            return

        last_run_at = 0.0

        while not stop_event.is_set():
            now = asyncio.get_running_loop().time()
            if now - last_run_at >= self.delivery_interval_seconds:
                try:
                    _ = await core_loop.run_once()
                except Exception as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    logger.exception("Bot delivery core loop run failed")
                last_run_at = now

            try:
                _ = await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.tick_interval_seconds,
                )
            except TimeoutError:
                continue
