"""Validate completed implementation-plan criteria are mapped to executable tests."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PLAN_PATH = Path("docs/implementation-plan.md")
COMPLETED_CRITERION_RE = re.compile(r"^\s*-\s*\[x\]\s+")
TESTS_BLOCK_RE = re.compile(r"\[Tests:\s*([^\]]+)\]")


def _collect_completed_criteria(lines: list[str]) -> list[tuple[int, str]]:
    criteria: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines, start=1):
        if COMPLETED_CRITERION_RE.search(line):
            criteria.append((line_number, line.rstrip("\n")))
    return criteria


def _parse_test_ids(criteria: list[tuple[int, str]]) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    nodeids: list[str] = []

    for line_number, criterion in criteria:
        match = TESTS_BLOCK_RE.search(criterion)
        if match is None:
            errors.append(
                f"{PLAN_PATH}:{line_number}: completed criterion missing "
                "[Tests: tests/...::test_...] mapping.",
            )
            continue

        raw_items = [item.strip() for item in match.group(1).split(",")]
        parsed_items = [item for item in raw_items if item]
        if not parsed_items:
            errors.append(f"{PLAN_PATH}:{line_number}: [Tests:] list is empty.")
            continue

        for item in parsed_items:
            if not item.startswith("tests/") or "::" not in item:
                errors.append(
                    f"{PLAN_PATH}:{line_number}: invalid test id '{item}'. "
                    "Expected tests/...::test_name.",
                )
                continue
            nodeids.append(item)

    return nodeids, errors


def _collect_pytest_nodeids() -> set[str]:
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        _write_stdout(result.stdout)
        _write_stderr(result.stderr)
        message = "pytest collection failed while validating plan criteria."
        raise RuntimeError(message)

    nodeids: set[str] = set()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("tests/"):
            nodeids.add(stripped)
    return nodeids


def _nodeid_exists(expected: str, collected: set[str]) -> bool:
    if expected in collected:
        return True
    param_prefix = f"{expected}["
    return any(nodeid.startswith(param_prefix) for nodeid in collected)


def _run_mapped_tests(nodeids: list[str]) -> int:
    unique_nodeids = sorted(set(nodeids))
    if not unique_nodeids:
        return 0
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-q", *unique_nodeids],
        check=False,
    )
    return result.returncode


def main() -> int:
    """Validate mapped tests for completed criteria and optionally run them."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="Execute mapped tests after validating mapping and collection.",
    )
    args = parser.parse_args()

    if not PLAN_PATH.exists():
        _write_stderr(f"{PLAN_PATH} does not exist.\n")
        return 1

    lines = PLAN_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    completed_criteria = _collect_completed_criteria(lines)
    if not completed_criteria:
        _write_stdout("No completed criteria found; nothing to validate.\n")
        return 0

    nodeids, errors = _parse_test_ids(completed_criteria)
    if errors:
        for error in errors:
            _write_stderr(f"{error}\n")
        return 1

    collected = _collect_pytest_nodeids()
    missing = [
        nodeid
        for nodeid in sorted(set(nodeids))
        if not _nodeid_exists(nodeid, collected)
    ]

    if missing:
        for nodeid in missing:
            _write_stderr(f"Missing mapped test nodeid: {nodeid}\n")
        return 1

    if args.run_tests:
        return _run_mapped_tests(nodeids)

    _write_stdout(
        f"Validated {len(completed_criteria)} completed criteria with "
        f"{len(set(nodeids))} mapped test ids.\n",
    )
    return 0


def _write_stdout(message: str) -> None:
    """Write message to stdout without using print."""
    _ = sys.stdout.write(message)


def _write_stderr(message: str) -> None:
    """Write message to stderr without using print."""
    _ = sys.stderr.write(message)


if __name__ == "__main__":
    raise SystemExit(main())
