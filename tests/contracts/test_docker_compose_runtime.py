"""Contract tests for Docker Compose runtime defaults and persistence."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = PROJECT_ROOT / "docker-compose.yml"


def _read_compose_file() -> str:
    if not COMPOSE_PATH.exists():
        raise AssertionError
    return COMPOSE_PATH.read_text(encoding="utf-8")


def test_docker_compose_uses_non_latest_image_tag() -> None:
    """Ensure Compose runtime references a pinned non-latest image tag."""
    compose_text = _read_compose_file()

    if "image:" not in compose_text:
        raise AssertionError
    if ":latest" in compose_text:
        raise AssertionError
    if "ghcr.io/<owner>/tca:0.1.0" not in compose_text:
        raise AssertionError


def test_docker_compose_persists_data_volume_for_db_and_backups() -> None:
    """Ensure /data is backed by a named volume for durable DB/backups state."""
    compose_text = _read_compose_file()

    required_fragments = [
        "volumes:",
        "- tca-data:/data",
        "tca-data:",
        "TCA_DB_PATH=/data/tca.db",
    ]
    for fragment in required_fragments:
        if fragment not in compose_text:
            raise AssertionError


def test_docker_compose_defaults_match_bind_and_mode_design_values() -> None:
    """Ensure default bind/mode env vars match design defaults."""
    compose_text = _read_compose_file()

    required_fragments = [
        "TCA_BIND=127.0.0.1",
        "TCA_MODE=secure-interactive",
    ]
    for fragment in required_fragments:
        if fragment not in compose_text:
            raise AssertionError
