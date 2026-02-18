"""Contract tests for the single-container runtime Dockerfile."""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE_PATH = PROJECT_ROOT / "Dockerfile"


def _read_dockerfile() -> str:
    if not DOCKERFILE_PATH.exists():
        raise AssertionError
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


def test_runtime_dockerfile_exists_and_uses_python_312_slim() -> None:
    """Ensure runtime image is pinned to Python 3.12 slim base."""
    dockerfile = _read_dockerfile()

    if "FROM python:3.12-slim" not in dockerfile:
        raise AssertionError
    if 'CMD ["python", "-m", "uvicorn", "tca.api.app:create_app"' not in dockerfile:
        raise AssertionError


def test_runtime_dockerfile_exposes_port_and_runs_on_8787() -> None:
    """Ensure container runtime contract serves API on port 8787."""
    dockerfile = _read_dockerfile()

    required_fragments = [
        "EXPOSE 8787",
        '--host", "0.0.0.0"',
        '--port", "8787"',
        "ENV TCA_DB_PATH=/data/tca.db",
    ]
    for fragment in required_fragments:
        if fragment not in dockerfile:
            raise AssertionError


def test_runtime_dockerfile_avoids_node_and_rust_toolchains() -> None:
    """Ensure Dockerfile does not require Node/Rust toolchains in runtime image."""
    dockerfile = _read_dockerfile().lower()

    forbidden_fragments = [
        "nodejs",
        "npm",
        "yarn",
        "rustc",
        "cargo",
    ]
    for fragment in forbidden_fragments:
        if fragment in dockerfile:
            raise AssertionError
