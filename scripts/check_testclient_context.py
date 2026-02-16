"""Enforce TestClient usage through context manager form in API and app tests."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

TEST_DIRS = (Path("tests/api"), Path("tests/app"))


def _is_testclient_call(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id == "TestClient"
    if isinstance(node.func, ast.Attribute):
        return node.func.attr == "TestClient"
    return False


def _with_context_call_nodes(tree: ast.AST) -> set[ast.Call]:
    context_calls: set[ast.Call] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.With | ast.AsyncWith):
            continue
        for item in node.items:
            context_expr = item.context_expr
            if isinstance(context_expr, ast.Call) and _is_testclient_call(context_expr):
                context_calls.add(context_expr)
    return context_calls


def _check_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path.as_posix())
    allowed_calls = _with_context_call_nodes(tree)

    errors: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_testclient_call(node):
            continue
        if node in allowed_calls:
            continue
        errors.append(
            f"{path}:{node.lineno}: TestClient must be used with "
            "'with TestClient(app) as client:'.",
        )
    return errors


def main() -> int:
    """Validate API/app tests instantiate TestClient only via with-context."""
    files: list[Path] = []
    for test_dir in TEST_DIRS:
        if test_dir.exists():
            files.extend(sorted(test_dir.glob("test_*.py")))

    errors: list[str] = []
    for path in files:
        errors.extend(_check_file(path))

    if errors:
        for error in errors:
            _write_stdout(f"{error}\n")
        return 1

    _write_stdout(
        f"Validated TestClient context-manager usage in {len(files)} test file(s).\n",
    )
    return 0


def _write_stdout(message: str) -> None:
    """Write message to stdout without using print."""
    _ = sys.stdout.write(message)


if __name__ == "__main__":
    raise SystemExit(main())
