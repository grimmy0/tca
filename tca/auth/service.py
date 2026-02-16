"""Authentication service helpers for Telegram login flows."""

from __future__ import annotations

from typing import Protocol


class OTPClient(Protocol):
    """Minimum client surface for requesting a Telegram login code."""

    async def send_code_request(self, phone: str) -> bool:
        """Request an OTP code for the provided phone number."""
        ...


async def request_login_code(client: OTPClient, phone: str) -> bool:
    """Request an OTP code through the injected Telegram client."""
    return await client.send_code_request(phone)
