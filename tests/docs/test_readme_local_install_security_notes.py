"""Contract checks for README local install and security notes."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
README_PATH = PROJECT_ROOT / "README.md"


def test_readme_includes_docker_and_local_run_paths() -> None:
    """Ensure README provides Docker and direct local run commands."""
    text = README_PATH.read_text(encoding="utf-8")
    required_fragments = [
        "docker compose up -d",
        "docker compose logs -f tca",
        "uv run uvicorn tca.api.app:create_app --factory --host 127.0.0.1 --port 8787",
    ]
    for fragment in required_fragments:
        if fragment not in text:
            raise AssertionError


def test_readme_documents_unlock_mode_tradeoff() -> None:
    """Ensure README explains secure-interactive versus auto-unlock behavior."""
    text = README_PATH.read_text(encoding="utf-8")
    required_fragments = [
        "secure-interactive",
        "auto-unlock",
        "lower security",
        "TCA_SECRET_FILE",
    ]
    for fragment in required_fragments:
        if fragment not in text:
            raise AssertionError


def test_readme_lists_telegram_credentials_prerequisites() -> None:
    """Ensure README lists Telegram API credential requirements."""
    text = README_PATH.read_text(encoding="utf-8")
    required_fragments = [
        "https://my.telegram.org",
        "api_id",
        "api_hash",
        "phone number for OTP verification",
    ]
    for fragment in required_fragments:
        if fragment not in text:
            raise AssertionError
