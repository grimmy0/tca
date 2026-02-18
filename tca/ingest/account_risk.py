"""Account risk escalation helpers for ingest polling."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tca.storage import (
        AccountPauseRecord,
        AccountPauseRepository,
        NotificationsRepository,
        SettingsRepository,
        WriterQueueProtocol,
    )
    from tca.storage.settings_repo import JSONValue

TimeProvider = Callable[[], datetime]

ACCOUNT_RISK_STATE_KEY_PREFIX = "ingest.account_risk_state."
ACCOUNT_RISK_NOTIFICATION_TYPE = "ingest.account_risk"
ACCOUNT_RISK_NOTIFICATION_SEVERITY = "high"
ACCOUNT_RISK_PAUSE_REASON = "account-risk"
ACCOUNT_RISK_WINDOW_SECONDS = 3600
ACCOUNT_RISK_THRESHOLD = 3


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _state_key(account_id: int) -> str:
    return f"{ACCOUNT_RISK_STATE_KEY_PREFIX}{account_id}"


def _encode_state(breaches: list[datetime]) -> dict[str, JSONValue]:
    return {"breaches": [breach.isoformat() for breach in breaches]}


def _decode_state(value: JSONValue | None) -> list[datetime]:
    if not isinstance(value, dict):
        return []
    breaches_obj = value.get("breaches")
    if not isinstance(breaches_obj, list):
        return []
    breaches: list[datetime] = []
    for item in breaches_obj:
        if not isinstance(item, str):
            continue
        try:
            parsed = datetime.fromisoformat(item)
        except ValueError:
            continue
        breaches.append(_normalize_datetime(parsed))
    return breaches


def _filter_recent(breaches: list[datetime], *, now: datetime) -> list[datetime]:
    cutoff = now - timedelta(seconds=ACCOUNT_RISK_WINDOW_SECONDS)
    return [breach for breach in breaches if breach >= cutoff]


async def record_account_risk_breach(  # noqa: PLR0913
    *,
    writer_queue: WriterQueueProtocol,
    settings_repository: SettingsRepository,
    pause_repository: AccountPauseRepository,
    notifications_repository: NotificationsRepository,
    account_id: int,
    breach_reason: str,
    time_provider: TimeProvider | None = None,
) -> AccountPauseRecord | None:
    """Record a risk breach and pause the account when threshold is exceeded."""
    now = _normalize_datetime(_utc_now() if time_provider is None else time_provider())

    async def _persist() -> AccountPauseRecord | None:
        pause_state = await pause_repository.get_pause_state(account_id=account_id)
        if pause_state is None:
            return None
        if pause_state.paused_at is not None:
            return pause_state

        setting_key = _state_key(account_id)
        setting = await settings_repository.get_by_key(key=setting_key)
        breaches = _decode_state(setting.value if setting is not None else None)
        breaches = _filter_recent(breaches, now=now)
        breaches.append(now)

        if len(breaches) < ACCOUNT_RISK_THRESHOLD:
            await _upsert_state(
                settings_repository=settings_repository,
                key=setting_key,
                breaches=breaches,
            )
            return None

        paused = await pause_repository.pause_account(
            account_id=account_id,
            reason=ACCOUNT_RISK_PAUSE_REASON,
            paused_at=now,
        )
        await _upsert_state(
            settings_repository=settings_repository,
            key=setting_key,
            breaches=[],
        )
        if paused is None:
            return None

        _ = await notifications_repository.create(
            notification_type=ACCOUNT_RISK_NOTIFICATION_TYPE,
            severity=ACCOUNT_RISK_NOTIFICATION_SEVERITY,
            message=(
                f"Account {account_id} paused after repeated risk events. "
                "Explicit resume required to continue polling."
            ),
            payload={
                "account_id": account_id,
                "breach_count": len(breaches),
                "breach_reason": breach_reason,
                "window_seconds": ACCOUNT_RISK_WINDOW_SECONDS,
                "paused_at": now.isoformat(),
            },
        )
        return paused

    return await writer_queue.submit(_persist)


async def _upsert_state(
    *,
    settings_repository: SettingsRepository,
    key: str,
    breaches: list[datetime],
) -> None:
    value = _encode_state(breaches)
    if await settings_repository.update(key=key, value=value) is None:
        _ = await settings_repository.create(key=key, value=value)
