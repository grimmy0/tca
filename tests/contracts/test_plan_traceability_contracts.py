"""Contract tests that anchor completed plan criteria to executable checks."""

from __future__ import annotations

import importlib
import logging
import sys
import tomllib
from pathlib import Path
from typing import cast

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_pyproject() -> dict[str, object]:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return cast("dict[str, object]", tomllib.load(handle))


def _expect_table(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError
    return cast("dict[str, object]", value)


def _expect_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        raise TypeError
    entries = cast("list[object]", value)
    for entry in entries:
        if not isinstance(entry, str):
            raise TypeError
    return cast("list[str]", entries)


def _dependency_declared(dependencies: list[str], package_name: str) -> bool:
    return any(
        dep.split(">=", maxsplit=1)[0].split("[", maxsplit=1)[0] == package_name
        for dep in dependencies
    )


def test_tca_package_layout_exists() -> None:
    """Ensure core package directories exist and are importable modules."""
    expected_modules = [
        "tca",
        "tca/api",
        "tca/auth",
        "tca/dedupe",
        "tca/ingest",
        "tca/normalize",
        "tca/ops",
        "tca/scheduler",
        "tca/storage",
        "tca/ui",
    ]
    for module_path in expected_modules:
        init_path = PROJECT_ROOT / module_path / "__init__.py"
        if not init_path.exists():
            raise AssertionError


def test_tca_importable() -> None:
    """Ensure the base package can be imported successfully."""
    _ = importlib.import_module("tca")


def test_tca_import_has_no_root_logger_side_effects() -> None:
    """Ensure importing tca package does not mutate root logger handlers."""
    root_logger = logging.getLogger()
    before_handler_ids = [id(handler) for handler in root_logger.handlers]

    _ = sys.modules.pop("tca", None)
    _ = importlib.import_module("tca")

    after_handler_ids = [id(handler) for handler in root_logger.handlers]
    if after_handler_ids != before_handler_ids:
        raise AssertionError


def test_runtime_dependencies_declared() -> None:
    """Ensure required runtime dependencies are declared in pyproject."""
    pyproject = _load_pyproject()
    project = _expect_table(pyproject.get("project"))
    dependencies = _expect_string_list(project.get("dependencies"))

    required_dependencies = [
        "fastapi",
        "uvicorn",
        "sqlalchemy",
        "aiosqlite",
        "alembic",
        "telethon",
        "jinja2",
        "rapidfuzz",
        "argon2-cffi",
        "cryptography",
    ]
    for dependency in required_dependencies:
        if not _dependency_declared(dependencies, dependency):
            raise AssertionError


def test_telethon_pin_uses_142_series() -> None:
    """Ensure Telethon dependency pin is constrained to 1.42.x."""
    pyproject = _load_pyproject()
    project = _expect_table(pyproject.get("project"))
    dependencies = _expect_string_list(project.get("dependencies"))

    telethon_dep = next(
        (dep for dep in dependencies if dep.startswith("telethon")),
        None,
    )
    if telethon_dep != "telethon>=1.42,<1.43":
        raise AssertionError


def test_uv_lock_exists_and_contains_telethon() -> None:
    """Ensure lock file exists and includes Telethon package metadata."""
    lock_path = PROJECT_ROOT / "uv.lock"
    if not lock_path.exists():
        raise AssertionError

    contents = lock_path.read_text(encoding="utf-8")
    if 'name = "telethon"' not in contents:
        raise AssertionError


def test_ruff_tooling_configured() -> None:
    """Ensure Ruff baseline tooling configuration is present."""
    pyproject = _load_pyproject()

    tool_config = _expect_table(pyproject.get("tool"))
    ruff_config = _expect_table(tool_config.get("ruff"))
    ruff_lint_config = _expect_table(ruff_config.get("lint"))
    ruff_select = _expect_string_list(ruff_lint_config.get("select"))
    if "E" not in ruff_select or "F" not in ruff_select:
        raise AssertionError


def test_pytest_tooling_configured() -> None:
    """Ensure pytest baseline tooling configuration is present."""
    pyproject = _load_pyproject()

    tool_config = _expect_table(pyproject.get("tool"))
    pytest_section = _expect_table(tool_config.get("pytest"))
    pytest_config = _expect_table(pytest_section.get("ini_options"))
    if pytest_config.get("asyncio_mode") != "auto":
        raise AssertionError


def test_mypy_strict_tooling_configured() -> None:
    """Ensure mypy strict mode remains enabled in project tooling."""
    pyproject = _load_pyproject()

    tool_config = _expect_table(pyproject.get("tool"))
    mypy_config = _expect_table(tool_config.get("mypy"))
    if mypy_config.get("strict") is not True:
        raise AssertionError


def test_shared_sqlite_fixture_exists_without_network_calls() -> None:
    """Ensure shared SQLite fixture exists and does not import network clients."""
    conftest_path = PROJECT_ROOT / "tests/conftest.py"
    text = conftest_path.read_text(encoding="utf-8")
    if "def sqlite_writer_pair" not in text:
        raise AssertionError
    if "requests" in text or "httpx" in text:
        raise AssertionError


def test_testing_guide_documents_sqlite_busy_reproduction() -> None:
    """Ensure guide includes reproducible SQLITE_BUSY test instructions."""
    guide_path = PROJECT_ROOT / "docs/testing-guide.md"
    text = guide_path.read_text(encoding="utf-8")
    required_fragments = ["SQLITE_BUSY", "BEGIN IMMEDIATE", "sqlite_writer_pair"]
    for fragment in required_fragments:
        if fragment not in text:
            raise AssertionError


def test_storage_concurrency_test_uses_shared_fixture() -> None:
    """Ensure C010-style concurrency test references shared fixture."""
    test_path = PROJECT_ROOT / "tests/storage/test_begin_immediate.py"
    text = test_path.read_text(encoding="utf-8")
    if "sqlite_writer_pair" not in text:
        raise AssertionError
