"""Fail if FastAPI route decorators omit explicit response models."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROUTES_DIR = Path("tca/api/routes")
HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _decorator_target_name(decorator: ast.expr) -> str | None:
    if isinstance(decorator, ast.Call):
        func = decorator.func
    else:
        return None

    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        return None
    if func.value.id != "router":
        return None
    return func.attr


def _has_explicit_response_model(decorator: ast.Call) -> bool:
    for keyword in decorator.keywords:
        if keyword.arg != "response_model":
            continue
        return not (
            isinstance(keyword.value, ast.Constant) and keyword.value.value is None
        )
    return False


def _check_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=path.as_posix())
    errors: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            target_name = _decorator_target_name(decorator)
            if target_name is None or target_name not in HTTP_METHODS:
                continue
            if isinstance(decorator, ast.Call) and _has_explicit_response_model(
                decorator,
            ):
                continue
            errors.append(
                f"{path}:{node.lineno}: route '{node.name}' uses "
                f"@router.{target_name}(...) without response_model=...",
            )
    return errors


def main() -> int:
    """Validate all API route decorators define explicit response models."""
    if not ROUTES_DIR.exists():
        _write_stdout(f"{ROUTES_DIR} not found; skipping check.\n")
        return 0

    route_files = sorted(ROUTES_DIR.rglob("*.py"))
    errors: list[str] = []
    for path in route_files:
        if path.name == "__init__.py":
            continue
        errors.extend(_check_file(path))

    if errors:
        for error in errors:
            _write_stdout(f"{error}\n")
        return 1

    _write_stdout(
        f"Validated response_model usage in {len(route_files)} route file(s).\n",
    )
    return 0


def _write_stdout(message: str) -> None:
    """Write message to stdout without using print."""
    _ = sys.stdout.write(message)


if __name__ == "__main__":
    raise SystemExit(main())
