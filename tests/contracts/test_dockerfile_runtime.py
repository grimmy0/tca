"""Contract tests for the single-container runtime Dockerfile."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE_PATH = PROJECT_ROOT / "Dockerfile"
HEALTH_TIMEOUT_SECONDS = 30.0


def _find_docker_binary() -> str | None:
    return shutil.which("docker")


def _read_dockerfile() -> str:
    if not DOCKERFILE_PATH.exists():
        raise AssertionError
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


def _docker_ready() -> bool:
    docker_bin = _find_docker_binary()
    if docker_bin is None:
        return False
    info_result = subprocess.run(  # noqa: S603
        [docker_bin, "info", "--format", "{{json .ServerVersion}}"],
        capture_output=True,
        check=False,
        text=True,
    )
    return info_result.returncode == 0


def _run_docker(args: list[str]) -> subprocess.CompletedProcess[str]:
    docker_bin = _find_docker_binary()
    if docker_bin is None:
        raise AssertionError
    return subprocess.run(  # noqa: S603
        [docker_bin, *args],
        capture_output=True,
        check=False,
        cwd=PROJECT_ROOT,
        text=True,
    )


@pytest.fixture(name="docker_image_tag")
def fixture_docker_image_tag() -> Iterator[str]:
    """Build a temporary Docker image tag for runtime container checks."""
    if not _docker_ready():
        pytest.skip("docker daemon unavailable in test environment")

    image_tag = f"tca-c083-{uuid.uuid4().hex[:12]}"
    build_result = _run_docker(["build", "-t", image_tag, "."])
    if build_result.returncode != 0:
        raise AssertionError(build_result.stderr.strip())

    yield image_tag
    _ = _run_docker(["image", "rm", "-f", image_tag])


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


def test_runtime_dockerfile_runs_as_non_root_user() -> None:
    """Ensure runtime image does not run the application as root."""
    dockerfile = _read_dockerfile()
    if "USER tca" not in dockerfile:
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


def test_runtime_dockerfile_builds_successfully(docker_image_tag: str) -> None:
    """Ensure the Docker image can be built successfully."""
    if not docker_image_tag.startswith("tca-c083-"):
        raise AssertionError


def test_runtime_dockerfile_container_serves_health_endpoint(
    docker_image_tag: str,
) -> None:
    """Ensure a container from this image serves GET /health with HTTP 200."""
    run_result = _run_docker(
        [
            "run",
            "-d",
            "-p",
            "127.0.0.1::8787",
            docker_image_tag,
        ],
    )
    if run_result.returncode != 0:
        raise AssertionError(run_result.stderr.strip())

    container_id = run_result.stdout.strip()
    if not container_id:
        raise AssertionError

    try:
        host_port = _resolve_host_port(container_id)
        response_payload = _wait_for_health_payload(host_port)
        if response_payload.get("status") != "ok":
            raise AssertionError
    finally:
        _ = _run_docker(["rm", "-f", container_id])


def _resolve_host_port(container_id: str) -> int:
    port_result = _run_docker(["port", container_id, "8787/tcp"])
    if port_result.returncode != 0:
        raise AssertionError(port_result.stderr.strip())

    lines = [line.strip() for line in port_result.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError
    first_line = lines[0]
    _, _, port_text = first_line.rpartition(":")
    if not port_text.isdigit():
        raise AssertionError
    return int(port_text)


def _wait_for_health_payload(host_port: int) -> dict[str, object]:
    url = f"http://127.0.0.1:{host_port}/health"
    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1.0) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
                if not isinstance(payload, dict):
                    raise TypeError
                return payload
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            last_error = exc
            time.sleep(0.5)

    if last_error is not None:
        raise AssertionError from last_error
    raise AssertionError
