"""Flag bare 'except Exception' handlers in async functions that swallow all errors.

In async code, catching Exception without a bare `raise` in the handler can silently
discard errors that should propagate — including future BaseException subclasses added
to a recoverable-errors tuple. This check requires that any `except Exception` handler
inside an `async def` body either:

  (a) contains a bare `raise` statement (re-raises on some path), or
  (b) contains an isinstance check for asyncio.CancelledError.

Test files are excluded (they often use try/except to assert exception behaviour).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SOURCE_DIRS = (Path("tca"),)


def _handler_has_reraise(handler: ast.ExceptHandler) -> bool:
    """Return True if the handler body contains a bare raise or CancelledError guard."""
    for stmt in handler.body:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Raise):
                return True
            # isinstance(exc, asyncio.CancelledError) guard
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "isinstance"
                and len(node.args) == 2  # noqa: PLR2004
            ):
                arg = node.args[1]
                # asyncio.CancelledError  or  CancelledError
                if isinstance(arg, ast.Attribute) and arg.attr == "CancelledError":
                    return True
                if isinstance(arg, ast.Name) and arg.id == "CancelledError":
                    return True
    return False


def _catches_bare_exception(handler: ast.ExceptHandler) -> bool:
    """Return True if the handler catches the bare Exception class."""
    typ = handler.type
    if typ is None:
        # bare `except:` — catches everything
        return True
    if isinstance(typ, ast.Name) and typ.id == "Exception":
        return True
    # except (A, Exception, B)
    if isinstance(typ, ast.Tuple):
        return any(
            isinstance(elt, ast.Name) and elt.id == "Exception" for elt in typ.elts
        )
    return False


def _check_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}: syntax error during parse: {exc}"]

    errors: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Try):
                continue
            for handler in child.handlers:
                if not _catches_bare_exception(handler):
                    continue
                if _handler_has_reraise(handler):
                    continue
                lineno = handler.lineno
                errors.append(
                    f"{path}:{lineno}: async function '{node.name}' has "
                    "'except Exception' with no re-raise or CancelledError guard. "
                    "Add `raise` on error paths or explicitly guard CancelledError.",
                )

    return errors


def main() -> int:
    """Check async functions in source dirs for unguarded broad exception catches."""
    errors: list[str] = []
    checked = 0

    for source_dir in SOURCE_DIRS:
        if not source_dir.exists():
            continue
        for path in sorted(source_dir.rglob("*.py")):
            # Skip test files — they intentionally catch exceptions to assert on them
            if path.name.startswith("test_") or "tests" in path.parts:
                continue
            checked += 1
            errors.extend(_check_file(path))

    if errors:
        for error in errors:
            _write_stderr(f"{error}\n")
        return 1

    _write_stdout(
        f"Validated async exception handling: {checked} source file(s) checked.\n",
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
