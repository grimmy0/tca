"""Fail if implementation plan execution records contain invalid commit SHAs.

Checks every '- Commit: `<value>`' line in the plan and rejects anything that
is not a valid lowercase hex SHA (7-40 chars).

Bad values seen in practice: NONE, PENDING, COMMIT_SHA_PLACEHOLDER, c016, 058.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PLAN_PATH = Path("docs/implementation-plan.md")

# Matches:  - Commit: `<value>`  (any indentation depth)
COMMIT_LINE_RE = re.compile(r"^\s+-\s+Commit:\s+`(?P<sha>[^`]+)`")

# Valid: 7-40 lowercase hex chars
VALID_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def main() -> int:
    """Validate all execution record commit SHAs in the implementation plan."""
    if not PLAN_PATH.exists():
        _write_stderr(f"{PLAN_PATH} does not exist; skipping check.\n")
        return 0

    lines = PLAN_PATH.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []

    for lineno, line in enumerate(lines, start=1):
        match = COMMIT_LINE_RE.match(line)
        if match is None:
            continue
        sha = match.group("sha")
        if not VALID_SHA_RE.match(sha):
            errors.append(
                f"{PLAN_PATH}:{lineno}: invalid execution-record SHA '{sha}'. "
                "Expected a 7-40 char lowercase hex SHA.",
            )

    if errors:
        for error in errors:
            _write_stderr(f"{error}\n")
        return 1

    commit_line_count = sum(1 for ln in lines if COMMIT_LINE_RE.match(ln))
    _write_stdout(
        f"Validated execution-record SHAs: {commit_line_count} commit lines checked.\n",
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
