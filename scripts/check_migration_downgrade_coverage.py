"""Fail if any migration test file exercises upgrades but omits a downgrade test.

Every test file under tests/migrations/ that contains the word "upgrade" (implying
it exercises a forward migration path) must also contain at least one test function
whose name includes "downgrade". This prevents the recurring pattern of ship-then-fix
where downgrade coverage is forgotten and added in a follow-up commit.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

MIGRATION_TESTS_DIR = Path("tests/migrations")

# Files that test migration infrastructure (tooling, env config) rather than
# specific migration schemas — downgrade coverage is not applicable to them.
_INFRASTRUCTURE_TEST_FILES = frozenset({"test_alembic_setup.py"})


def _test_function_names(path: Path) -> list[str]:
    """Return names of all top-level test functions in a file."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        _write_stderr(f"{path}: syntax error during parse: {exc}\n")
        return []
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        and node.name.startswith("test_")
    ]


def main() -> int:
    """Check migration test files with upgrade tests also have downgrade tests."""
    if not MIGRATION_TESTS_DIR.exists():
        _write_stdout(f"{MIGRATION_TESTS_DIR} not found; skipping check.\n")
        return 0

    test_files = sorted(MIGRATION_TESTS_DIR.glob("test_*.py"))
    errors: list[str] = []
    checked = 0

    for path in test_files:
        if path.name in _INFRASTRUCTURE_TEST_FILES:
            continue

        source = path.read_text(encoding="utf-8")

        # Only inspect files that exercise alembic upgrade paths
        if "upgrade" not in source:
            continue

        checked += 1
        test_names = _test_function_names(path)
        # Match "downgrade", "downgrading", "downgrades", etc. via common stem.
        has_downgrade = any("downgrad" in name for name in test_names)
        if not has_downgrade:
            errors.append(
                f"{path}: file exercises 'upgrade' but contains no test function "
                "with 'downgrad' in its name. Add a downgrade coverage test.",
            )

    if errors:
        for error in errors:
            _write_stderr(f"{error}\n")
        return 1

    _write_stdout(
        f"Validated migration downgrade coverage: "
        f"{checked}/{len(test_files)} migration test file(s) checked.\n",
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
