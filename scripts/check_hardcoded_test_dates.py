"""Fail if test files contain hardcoded future-year datetime/date literals.

Using datetime(2026, 2, 17, ...) as a 'future' reference in tests causes tests to
break silently once that date passes. The correct pattern is:

    datetime.now(timezone.utc) + timedelta(minutes=30)

This check flags any datetime() or date() constructor call in test files where the
first argument is a year integer >= 2024, unless the call result is immediately used
with timedelta arithmetic (a pattern we allow since it's documenting a base offset
rather than claiming a specific future point).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

TEST_DIRS = (Path("tests"),)

# Year threshold: flag any hardcoded year that could plausibly be "future"
_FUTURE_YEAR_THRESHOLD = 2024

# Constructor names that take (year, month, day, ...) as first positional arg
_DATE_CONSTRUCTOR_NAMES = frozenset({"datetime", "date"})


def _is_date_constructor(call: ast.Call) -> bool:
    """Return True if the call looks like datetime(...) or date(...)."""
    func = call.func
    if isinstance(func, ast.Name) and func.id in _DATE_CONSTRUCTOR_NAMES:
        return True
    return isinstance(func, ast.Attribute) and func.attr in _DATE_CONSTRUCTOR_NAMES


def _first_arg_is_large_year(call: ast.Call) -> int | None:
    """Return the year if the first positional arg is a large integer, else None."""
    if not call.args:
        return None
    first = call.args[0]
    if (
        isinstance(first, ast.Constant)
        and isinstance(first.value, int)
        and first.value >= _FUTURE_YEAR_THRESHOLD
    ):
        return first.value
    return None


def _call_is_inside_binop_with_timedelta(call: ast.Call, tree: ast.AST) -> bool:
    """Return True if the call node appears as the direct left operand of a +/- BinOp.

    This allows patterns like datetime(2024, 1, 1) + timedelta(days=30) where the
    datetime is an anchor, not a claim about the future.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp):
            continue
        if not isinstance(node.op, ast.Add | ast.Sub):
            continue
        if node.left is call:
            right = node.right
            # Check if right side involves timedelta
            if isinstance(right, ast.Call):
                func = right.func
                if isinstance(func, ast.Name) and func.id == "timedelta":
                    return True
                if isinstance(func, ast.Attribute) and func.attr == "timedelta":
                    return True
    return False


def _check_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error during parse: {exc}"]

    errors: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_date_constructor(node):
            continue
        year = _first_arg_is_large_year(node)
        if year is None:
            continue
        if _call_is_inside_binop_with_timedelta(node, tree):
            continue
        errors.append(
            f"{path}:{node.lineno}: hardcoded year {year} in "
            "datetime/date constructor. "
            "Use `datetime.now(timezone.utc) + timedelta(...)` for future "
            "references so the test does not become stale.",
        )

    return errors


def main() -> int:
    """Check test files for hardcoded future-year datetime/date literals."""
    errors: list[str] = []
    checked = 0

    for test_dir in TEST_DIRS:
        if not test_dir.exists():
            continue
        for path in sorted(test_dir.rglob("test_*.py")):
            checked += 1
            errors.extend(_check_file(path))

    if errors:
        for error in errors:
            _write_stderr(f"{error}\n")
        return 1

    _write_stdout(
        f"Validated datetime literals: {checked} test file(s) checked.\n",
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
