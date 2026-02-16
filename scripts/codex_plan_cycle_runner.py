#!/usr/bin/env python3
"""Verbose automation loop for implementation-plan execution via Codex CLI."""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

ITEM_HEADER_RE = re.compile(r"^###\s+(C\d+[A-Z]?)\s*-\s*(.+?)\s*$")
CRITERION_RE = re.compile(r"^\s*-\s*\[(?P<mark>[xX ])\]\s+(?P<text>.+?)\s*$")
VERIFICATION_HEADER_RE = re.compile(r"^\s*-\s*Verification:\s*$")
VERIFICATION_COMMAND_RE = re.compile(r"^\s*-\s*`(?P<command>[^`]+)`\s*$")
QUOTA_ERROR_PATTERNS = (
    re.compile(r"\binsufficient[_ ]quota\b", re.IGNORECASE),
    re.compile(r"\brate[ -]?limit(?:ed)?\b", re.IGNORECASE),
    re.compile(r"\btoo many requests\b", re.IGNORECASE),
    re.compile(r"\busage limit\b", re.IGNORECASE),
    re.compile(r"\bquota exceeded\b", re.IGNORECASE),
    re.compile(r"\bexceeded\b.*\b(rate|quota|usage)\b", re.IGNORECASE),
    re.compile(r"\byou(?:'ve| have) reached .*limit\b", re.IGNORECASE),
    re.compile(r"\bout of credits?\b", re.IGNORECASE),
    re.compile(r"\b429\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class PlanCriterion:
    """One checkbox criterion inside a plan item."""

    line_number: int
    checked: bool
    text: str


@dataclass(frozen=True)
class PlanItem:
    """Parsed implementation-plan item."""

    identifier: str
    title: str
    header_line: int
    criteria: tuple[PlanCriterion, ...]
    verification_commands: tuple[str, ...]

    @property
    def unchecked_criteria(self) -> tuple[PlanCriterion, ...]:
        """Return unchecked criteria for this item."""
        return tuple(criterion for criterion in self.criteria if not criterion.checked)

    @property
    def is_complete(self) -> bool:
        """A plan item is complete when all its criteria are checked."""
        if not self.criteria:
            return False
        return all(criterion.checked for criterion in self.criteria)


@dataclass(frozen=True)
class PlanStats:
    """Plan progress counters."""

    total_items: int
    complete_items: int
    total_criteria: int
    checked_criteria: int

    @property
    def complete_items_pct(self) -> float:
        """Completion percentage over items."""
        if self.total_items == 0:
            return 0.0
        return (self.complete_items / self.total_items) * 100.0

    @property
    def checked_criteria_pct(self) -> float:
        """Completion percentage over criteria."""
        if self.total_criteria == 0:
            return 0.0
        return (self.checked_criteria / self.total_criteria) * 100.0


@dataclass(frozen=True)
class CommandResult:
    """Captured command result."""

    returncode: int
    output: str


@dataclass(frozen=True)
class CodexStepResult:
    """Result of one codex step (implement/review)."""

    returncode: int
    output: str
    last_message: str
    prompt_path: Path
    output_path: Path
    last_message_path: Path


class TeeLogger:
    """Very chatty logger that writes to stdout and a persistent log file."""

    def __init__(self, log_file_path: Path) -> None:
        """Initialize logger and open persistent log file handle."""
        self.log_file_path = log_file_path
        self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: TextIO = self.log_file_path.open("a", encoding="utf-8")
        self.log(f"Log file: {self.log_file_path}")

    def close(self) -> None:
        """Close underlying log stream."""
        self._fh.close()

    def log(self, message: str) -> None:
        """Write one timestamped line to stdout and log file."""
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        line = f"[{timestamp}] {message}"
        _ = sys.stdout.write(f"{line}\n")
        _ = self._fh.write(f"{line}\n")
        self._fh.flush()

    def section(self, title: str) -> None:
        """Log a visually distinct section heading."""
        separator = "=" * 80
        self.log(separator)
        self.log(title)
        self.log(separator)

    def command(self, label: str, command: list[str], cwd: Path) -> None:
        """Log command invocation details."""
        rendered = shlex.join(command)
        self.log(f"{label} | cwd={cwd}")
        self.log(f"{label} | cmd={rendered}")


def parse_plan(plan_path: Path) -> list[PlanItem]:  # noqa: C901
    """Parse all Cxxx plan items and their checkbox criteria."""
    lines = plan_path.read_text(encoding="utf-8").splitlines()
    items: list[PlanItem] = []

    current_id: str | None = None
    current_title = ""
    current_header_line = 0
    current_criteria: list[PlanCriterion] = []
    current_verification_commands: list[str] = []
    in_verification_block = False

    def flush_current() -> None:
        if current_id is None:
            return
        items.append(
            PlanItem(
                identifier=current_id,
                title=current_title,
                header_line=current_header_line,
                criteria=tuple(current_criteria),
                verification_commands=tuple(current_verification_commands),
            ),
        )

    for line_number, line in enumerate(lines, start=1):
        header_match = ITEM_HEADER_RE.match(line)
        if header_match:
            flush_current()
            current_id = header_match.group(1)
            current_title = header_match.group(2)
            current_header_line = line_number
            current_criteria = []
            current_verification_commands = []
            in_verification_block = False
            continue

        if current_id is None:
            continue

        criterion_match = CRITERION_RE.match(line)
        if criterion_match:
            mark = criterion_match.group("mark").lower()
            current_criteria.append(
                PlanCriterion(
                    line_number=line_number,
                    checked=mark == "x",
                    text=criterion_match.group("text"),
                ),
            )

        if VERIFICATION_HEADER_RE.match(line):
            in_verification_block = True
            continue

        if in_verification_block:
            command_match = VERIFICATION_COMMAND_RE.match(line)
            if command_match:
                current_verification_commands.append(command_match.group("command"))
                continue
            if line.strip().startswith("- ") and "Verification" not in line:
                in_verification_block = False

    flush_current()
    return items


def compute_stats(items: list[PlanItem]) -> PlanStats:
    """Compute aggregate plan progress."""
    total_criteria = sum(len(item.criteria) for item in items)
    checked_criteria = sum(
        1 for item in items for criterion in item.criteria if criterion.checked
    )
    complete_items = sum(1 for item in items if item.is_complete)
    return PlanStats(
        total_items=len(items),
        complete_items=complete_items,
        total_criteria=total_criteria,
        checked_criteria=checked_criteria,
    )


def next_pending_item(items: list[PlanItem]) -> PlanItem | None:
    """Return first item that still has unchecked criteria."""
    for item in items:
        if item.unchecked_criteria:
            return item
    return None


def resolve_executable(command_name: str) -> str:
    """Resolve executable path or fail with a clear error."""
    resolved = shutil.which(command_name)
    if resolved is None:
        message = f"Executable not found on PATH: {command_name}"
        raise FileNotFoundError(message)
    return resolved


def run_capture(
    *,
    command: list[str],
    cwd: Path,
    logger: TeeLogger,
    label: str,
) -> CommandResult:
    """Run command and capture stdout+stderr for diagnostics."""
    logger.command(label, command, cwd)
    completed = subprocess.run(  # noqa: S603
        command,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.stdout:
        for line in completed.stdout.splitlines():
            logger.log(f"{label} | stdout | {line}")
    if completed.stderr:
        for line in completed.stderr.splitlines():
            logger.log(f"{label} | stderr | {line}")
    logger.log(f"{label} | exit_code={completed.returncode}")
    merged_output = "\n".join(
        chunk for chunk in [completed.stdout.strip(), completed.stderr.strip()] if chunk
    )
    return CommandResult(returncode=completed.returncode, output=merged_output)


def run_stream(
    *,
    command: list[str],
    cwd: Path,
    stdin_text: str | None,
    logger: TeeLogger,
    label: str,
) -> CommandResult:
    """Run command and stream output line-by-line to logger."""
    logger.command(label, command, cwd)
    started_at = time.monotonic()
    process = subprocess.Popen(  # noqa: S603
        command,
        cwd=cwd,
        stdin=subprocess.PIPE if stdin_text is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    if stdin_text is not None and process.stdin is not None:
        _ = process.stdin.write(stdin_text)
        process.stdin.close()

    output_lines: list[str] = []
    if process.stdout is not None:
        for line in process.stdout:
            output_lines.append(line)
            logger.log(f"{label} | live | {line.rstrip()}")

    return_code = process.wait()
    elapsed = time.monotonic() - started_at
    logger.log(f"{label} | exit_code={return_code} | elapsed_sec={elapsed:.2f}")
    return CommandResult(returncode=return_code, output="".join(output_lines))


def read_last_message(last_message_path: Path) -> str:
    """Read codex final message file if present."""
    if not last_message_path.exists():
        return ""
    return last_message_path.read_text(encoding="utf-8").strip()


def is_plan_done(last_message: str) -> bool:
    """Return True when codex replied with PLAN IS DONE sentinel."""
    stripped = last_message.strip()
    if stripped == "PLAN IS DONE":
        return True
    return any(line.strip() == "PLAN IS DONE" for line in last_message.splitlines())


def detect_quota_issue(*texts: str) -> str | None:
    """Return a matched line when output indicates quota/rate-limit exhaustion."""
    for text in texts:
        for line in text.splitlines():
            for pattern in QUOTA_ERROR_PATTERNS:
                if pattern.search(line):
                    return line.strip()
    return None


def run_quota_probe(
    *,
    codex_bin: str,
    repo_root: Path,
    model: str | None,
    logger: TeeLogger,
) -> bool:
    """Run a minimal codex exec to test whether API limits have reset."""
    command: list[str] = [
        codex_bin,
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--cd",
        str(repo_root),
        "-",
    ]
    if model is not None:
        command.extend(["--model", model])
    try:
        completed = subprocess.run(  # noqa: S603
            command,
            cwd=repo_root,
            input="Reply with exactly one word: OK",
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        logger.log("Quota probe timed out after 120s — treating as still limited.")
        return False
    if completed.returncode == 0:
        return True
    merged = f"{completed.stdout}\n{completed.stderr}"
    if detect_quota_issue(merged) is not None:
        return False
    # Non-quota failure — log but treat as still limited to be safe.
    logger.log(
        f"Quota probe failed with code {completed.returncode} "
        f"(non-quota error) — treating as still limited.",
    )
    return False


def wait_for_quota_reset(  # noqa: PLR0913
    *,
    codex_bin: str,
    repo_root: Path,
    model: str | None,
    logger: TeeLogger,
    interval: float,
    max_attempts: int,
    step_name: str,
    matched_line: str,
) -> bool:
    """Sleep-and-probe until quota resets. Returns True if recovered."""
    if max_attempts <= 0:
        return False
    logger.section(f"Quota hit during '{step_name}' - entering wait loop")
    logger.log(f"Matched: {matched_line}")
    for attempt in range(1, max_attempts + 1):
        logger.log(
            f"Probe attempt {attempt}/{max_attempts} in {interval:.0f}s...",
        )
        time.sleep(interval)
        if run_quota_probe(
            codex_bin=codex_bin,
            repo_root=repo_root,
            model=model,
            logger=logger,
        ):
            logger.log("Quota probe succeeded - resuming.")
            return True
        logger.log("Still limited.")
    logger.log("All quota wait attempts exhausted.")
    return False


def graceful_quota_exit(
    *,
    logger: TeeLogger,
    step_name: str,
    matched_line: str,
) -> int:
    """Log quota hit clearly and stop with dedicated non-zero code."""
    logger.section("Quota/Limit Hit - Graceful Exit")
    logger.log(
        f"Codex quota/rate limit detected during step '{step_name}'.",
    )
    logger.log(f"Matched output line: {matched_line}")
    logger.log(
        "Stopping run gracefully. You can resume later once limits reset.",
    )
    logger.log(f"Detailed logs: {logger.log_file_path}")
    _ = sys.stdout.write(
        "STOPPED: Codex quota/rate limit reached "
        f"during '{step_name}'. See {logger.log_file_path}\n",
    )
    return 4


def git_output_or_fail(
    *,
    git_bin: str,
    cwd: Path,
    logger: TeeLogger,
    label: str,
    args: list[str],
) -> str:
    """Run git command and return stdout; raise on non-zero exit."""
    result = run_capture(
        command=[git_bin, *args],
        cwd=cwd,
        logger=logger,
        label=label,
    )
    if result.returncode != 0:
        message = f"{label} failed."
        raise RuntimeError(message)
    return result.output.strip()


def maybe_git_output(
    *,
    git_bin: str,
    cwd: Path,
    logger: TeeLogger,
    label: str,
    args: list[str],
) -> str | None:
    """Run git command and return stdout when successful."""
    result = run_capture(
        command=[git_bin, *args],
        cwd=cwd,
        logger=logger,
        label=label,
    )
    if result.returncode != 0:
        return None
    return result.output.strip()


def format_criteria(criteria: tuple[PlanCriterion, ...]) -> str:
    """Render criteria bullets for prompt context."""
    if not criteria:
        return "- (none parsed)"
    lines = [
        f"- {criterion.text} (line {criterion.line_number})" for criterion in criteria
    ]
    return "\n".join(lines)


def format_verification_commands(commands: tuple[str, ...]) -> str:
    """Render verification command bullets for prompt context."""
    if not commands:
        return "- (none declared in item)"
    return "\n".join(f"- `{command}`" for command in commands)


def build_implementation_prompt(
    *,
    repo_root: Path,
    plan_path: Path,
    design_path: Path,
    item: PlanItem | None,
) -> str:
    """Create highly explicit implementation prompt for Codex."""
    if item is None:
        return (
            textwrap.dedent(
                f"""
            You are running in repository: {repo_root}

            Task:
            1. Inspect {plan_path} and verify whether any acceptance criterion
               remains unchecked.
            2. If no pending criteria remain, make no file changes and output exactly:
            PLAN IS DONE
            3. If you find pending criteria, implement exactly the first pending
               plan item,
               update plan traceability, run relevant checks, and commit it.

            Hard constraints:
            - Follow commit-atomic behavior from {plan_path}.
            - Follow architecture constraints from {design_path}.
            - Do not batch multiple plan items.
            - Do not push in this step.
            """,
            ).strip()
            + "\n"
        )

    unchecked = format_criteria(item.unchecked_criteria)
    verification_commands = format_verification_commands(item.verification_commands)
    return (
        textwrap.dedent(
            f"""
        You are running in repository: {repo_root}

        Primary objective:
        Implement exactly one commit-atomic item from {plan_path}.

        Target item (already selected):
        - ID: {item.identifier}
        - Title: {item.title}
        - Header line: {item.header_line}

        Unchecked acceptance criteria for target item:
        {unchecked}

        Verification commands listed in the target item:
        {verification_commands}

        Hard constraints:
        1. Read {plan_path} and {design_path}.
        2. Implement ONLY {item.identifier}. Do not start any later plan item.
        3. Update the target item criteria to [x] with explicit
           [Tests: tests/...::test_...]
           mappings on completed criteria.
        4. Update the target item Execution record
           (date, commit hash, verification summary).
        5. Run relevant tests/checks and use item verification commands as baseline.
        6. Keep changes focused and commit-atomic.
        7. Create exactly one implementation commit.
        8. Do NOT push in this step.
        9. If you discover no pending work exists, output exactly PLAN IS DONE.

        Final response format (exact keys):
        IMPLEMENTED_ITEM: {item.identifier}
        IMPLEMENTED_TITLE: {item.title}
        IMPLEMENTATION_COMMIT: <sha>
        IMPLEMENTATION_SUMMARY: <short summary>
        """,
        ).strip()
        + "\n"
    )


def build_review_prompt(  # noqa: PLR0913
    *,
    repo_root: Path,
    plan_path: Path,
    implemented_item: PlanItem | None,
    implemented_commit: str,
    branch: str,
    upstream: str | None,
) -> str:
    """Create strict review+fix+push prompt."""
    item_label = (
        f"{implemented_item.identifier} - {implemented_item.title}"
        if implemented_item is not None
        else "unknown plan item"
    )
    upstream_label = upstream if upstream is not None else "(no upstream configured)"
    return (
        textwrap.dedent(
            f"""
        You are running in repository: {repo_root}

        Strict review target:
        - Plan item: {item_label}
        - Commit SHA: {implemented_commit}
        - Branch: {branch}
        - Upstream: {upstream_label}

        Task:
        1. Perform the strictest possible code review of commit {implemented_commit}
           against its parent.
        2. Find all issues (correctness, reliability, edge cases, security, typing,
           linting, tests, traceability, and plan/spec compliance).
        3. Fix all findings in code/docs/tests as needed.
        4. Run relevant checks to validate the fixes.
        5. Commit review-driven fixes if changes were necessary (no empty commit).
        6. Push current branch to its upstream.

        Constraints:
        - Do not amend, rebase, or rewrite history.
        - Keep follow-up changes scoped to review findings.
        - If there are no findings that require code changes, still push.

        Context files:
        - {plan_path}

        Final response format (exact keys):
        REVIEW_TARGET_COMMIT: {implemented_commit}
        REVIEW_FINDINGS_FIXED: <integer>
        REVIEW_FIX_COMMIT: <sha|NONE>
        PUSH_STATUS: <OK|FAILED: reason>
        """,
        ).strip()
        + "\n"
    )


def build_docs_review_prompt(*, repo_root: Path) -> str:
    """Create a strict technical-writing docs review prompt for Codex."""
    return (
        textwrap.dedent(
            f"""
        You are running in repository: {repo_root}

        Task: Review and update ALL project documentation to match the current
        state of the codebase.

        Scope — read every file in these locations:
        - docs/
        - README.md
        - CLAUDE.md
        - GEMINI.md
        - .github/pull_request_template.md

        Process:
        1. Read the current codebase thoroughly to understand what is actually
           implemented (modules, APIs, CLI flags, config, dependencies, etc.).
        2. Do web research for any libraries, APIs, or patterns referenced in the
           code that you are not 100% certain about.
        3. Compare each doc against reality — identify stale, inaccurate, or
           missing content.
        4. Update, add, or remove doc sections as needed.
        5. Commit all doc changes with a clear commit message.
        6. Push to upstream.

        Hard constraints:
        - NEVER assume behavior — verify by reading code and doing web research.
        - Follow modern technical writing practices: active voice,
          task-oriented structure, concrete examples, no filler.
        - Preserve existing doc structure and formatting conventions.
        - Only change what needs changing — no cosmetic rewrites.
        - Do not modify any non-documentation files.
        - If all docs are already accurate, make no changes and push nothing.

        Final response format:
        DOCS_REVIEW_FILES_CHANGED: <integer>
        DOCS_REVIEW_COMMIT: <sha|NONE>
        PUSH_STATUS: <OK|FAILED: reason|SKIPPED>
        """,
        ).strip()
        + "\n"
    )


def run_docs_review(  # noqa: PLR0913
    *,
    codex_bin: str,
    repo_root: Path,
    model: str | None,
    cycle_number: int,
    logs_root: Path,
    logger: TeeLogger,
    quota_wait_interval: float,
    max_quota_waits: int,
) -> None:
    """Run a docs review step with quota retry.

    Non-critical: logs warnings on failure.
    """
    logger.section(f"Docs Review (after cycle {cycle_number})")
    prompt = build_docs_review_prompt(repo_root=repo_root)
    while True:
        result = run_codex_step(
            codex_bin=codex_bin,
            repo_root=repo_root,
            model=model,
            cycle_number=cycle_number,
            step_name="docs_review",
            prompt=prompt,
            logs_root=logs_root,
            logger=logger,
        )
        if result.returncode == 0:
            logger.log("Docs review step completed successfully.")
            return
        quota_hit = detect_quota_issue(result.output, result.last_message)
        if quota_hit is not None:
            if wait_for_quota_reset(
                codex_bin=codex_bin,
                repo_root=repo_root,
                model=model,
                logger=logger,
                interval=quota_wait_interval,
                max_attempts=max_quota_waits,
                step_name="docs_review",
                matched_line=quota_hit,
            ):
                continue
            logger.log(
                "WARNING: Docs review quota exhausted — skipping. "
                f"Matched: {quota_hit}",
            )
            return
        logger.log(
            f"WARNING: Docs review step failed with code {result.returncode} "
            "— skipping (non-critical).",
        )
        return


def run_codex_step(  # noqa: PLR0913
    *,
    codex_bin: str,
    repo_root: Path,
    model: str | None,
    cycle_number: int,
    step_name: str,
    prompt: str,
    logs_root: Path,
    logger: TeeLogger,
) -> CodexStepResult:
    """Run one codex exec call and persist prompt/output artifacts."""
    step_dir = logs_root / f"cycle_{cycle_number:03d}_{step_name}"
    step_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = step_dir / "prompt.txt"
    output_path = step_dir / "codex_output.log"
    last_message_path = step_dir / "last_message.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    command: list[str] = [
        codex_bin,
        "exec",
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--cd",
        str(repo_root),
        "--output-last-message",
        str(last_message_path),
    ]
    if model is not None:
        command.extend(["--model", model])
    command.append("-")

    result = run_stream(
        command=command,
        cwd=repo_root,
        stdin_text=prompt,
        logger=logger,
        label=f"codex.{step_name}",
    )
    output_path.write_text(result.output, encoding="utf-8")
    last_message = read_last_message(last_message_path)
    if last_message:
        logger.log(
            f"codex.{step_name} | last_message_start\n"
            f"{last_message}\n"
            "codex.{step_name} | last_message_end",
        )
    else:
        logger.log(f"codex.{step_name} | no last message captured.")

    return CodexStepResult(
        returncode=result.returncode,
        output=result.output,
        last_message=last_message,
        prompt_path=prompt_path,
        output_path=output_path,
        last_message_path=last_message_path,
    )


def parse_args() -> argparse.Namespace:
    """Build CLI for the runner."""
    parser = argparse.ArgumentParser(
        description=(
            "Cycle Codex over implementation-plan items with strict review follow-up."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root where git + codex commands should run.",
    )
    parser.add_argument(
        "--plan-path",
        type=Path,
        default=Path("docs/implementation-plan.md"),
        help="Path to implementation plan (relative to repo root by default).",
    )
    parser.add_argument(
        "--design-path",
        type=Path,
        default=Path("docs/option-a-local-design.md"),
        help="Path to design doc for prompt context.",
    )
    parser.add_argument(
        "--codex-bin",
        default="codex",
        help="Codex executable name/path.",
    )
    parser.add_argument(
        "--git-bin",
        default="git",
        help="Git executable name/path.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Optional codex model override passed to codex exec.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=200,
        help="Maximum implement/review cycles before stopping.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Optional delay between cycles.",
    )
    parser.add_argument(
        "--quota-wait-interval",
        type=float,
        default=3600.0,
        help="Seconds between quota-reset probes (default: 3600 = 1 hour).",
    )
    parser.add_argument(
        "--max-quota-waits",
        type=int,
        default=0,
        help=(
            "Max probe attempts before giving up on quota reset. "
            "0 = exit immediately on quota hit (default)."
        ),
    )
    parser.add_argument(
        "--docs-review-interval",
        type=int,
        default=5,
        help=(
            "Run docs review every N cycles. 0 = disabled. "
            "Also runs once when the plan completes (default: 5)."
        ),
    )
    parser.add_argument(
        "--allow-dirty-start",
        action="store_true",
        help="Allow starting when git working tree already has uncommitted changes.",
    )
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=Path("logs/codex-plan-cycle-runner"),
        help="Directory for run logs and per-step codex artifacts.",
    )
    return parser.parse_args()


def main() -> int:  # noqa: C901, PLR0911, PLR0912, PLR0915
    """Entrypoint for verbose codex plan runner."""
    args = parse_args()
    repo_root = args.repo_root.resolve()
    plan_path = (repo_root / args.plan_path).resolve()
    design_path = (repo_root / args.design_path).resolve()
    logs_root = (repo_root / args.logs_dir).resolve()
    run_id = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    log_file = logs_root / f"run_{run_id}.log"

    logger = TeeLogger(log_file)
    try:
        logger.section("Codex Plan Cycle Runner Startup")
        logger.log(f"repo_root={repo_root}")
        logger.log(f"plan_path={plan_path}")
        logger.log(f"design_path={design_path}")
        logger.log(f"logs_root={logs_root}")
        logger.log(f"max_cycles={args.max_cycles}")
        logger.log(f"sleep_seconds={args.sleep_seconds}")
        logger.log(f"allow_dirty_start={args.allow_dirty_start}")
        logger.log(f"quota_wait_interval={args.quota_wait_interval}")
        logger.log(f"max_quota_waits={args.max_quota_waits}")
        logger.log(f"docs_review_interval={args.docs_review_interval}")
        logger.log("mode=yolo (--dangerously-bypass-approvals-and-sandbox)")

        if not repo_root.exists():
            logger.log("ERROR: repository root does not exist.")
            return 1
        if not plan_path.exists():
            logger.log("ERROR: plan file does not exist.")
            return 1
        if not design_path.exists():
            logger.log("ERROR: design file does not exist.")
            return 1

        codex_bin = resolve_executable(args.codex_bin)
        git_bin = resolve_executable(args.git_bin)
        logger.log(f"resolved codex_bin={codex_bin}")
        logger.log(f"resolved git_bin={git_bin}")

        git_status_before = run_capture(
            command=[git_bin, "status", "--short", "--branch"],
            cwd=repo_root,
            logger=logger,
            label="git.status.startup",
        )
        if git_status_before.output:
            logger.log("git.status.startup summary:")
            for line in git_status_before.output.splitlines():
                logger.log(f"git.status.startup | {line}")
        dirty_at_start = bool(
            maybe_git_output(
                git_bin=git_bin,
                cwd=repo_root,
                logger=logger,
                label="git.porcelain.startup",
                args=["status", "--porcelain"],
            ),
        )
        if dirty_at_start and not args.allow_dirty_start:
            logger.log(
                "ERROR: working tree is dirty at startup. "
                "Use --allow-dirty-start to override.",
            )
            return 1
        if dirty_at_start:
            logger.log(
                "WARNING: working tree is dirty at startup. "
                "Runner will continue because --allow-dirty-start was set.",
            )

        branch = git_output_or_fail(
            git_bin=git_bin,
            cwd=repo_root,
            logger=logger,
            label="git.branch",
            args=["rev-parse", "--abbrev-ref", "HEAD"],
        )
        upstream = maybe_git_output(
            git_bin=git_bin,
            cwd=repo_root,
            logger=logger,
            label="git.upstream",
            args=["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        )
        logger.log(f"git.branch.current={branch}")
        upstream_log_value = upstream if upstream is not None else "(none)"
        logger.log(f"git.branch.upstream={upstream_log_value}")

        for cycle_number in range(1, args.max_cycles + 1):
            logger.section(f"Cycle {cycle_number} Start")

            items = parse_plan(plan_path)
            stats = compute_stats(items)
            pending_item = next_pending_item(items)

            logger.log(
                "Plan progress: "
                f"items={stats.complete_items}/{stats.total_items} "
                f"({stats.complete_items_pct:.2f}%), "
                f"criteria={stats.checked_criteria}/{stats.total_criteria} "
                f"({stats.checked_criteria_pct:.2f}%).",
            )
            if pending_item is None:
                logger.log(
                    "Parser sees no pending item. Codex implement step will verify and "
                    "should return PLAN IS DONE.",
                )
            else:
                logger.log(
                    f"Next pending item: {pending_item.identifier} - "
                    f"{pending_item.title} (line {pending_item.header_line}).",
                )
                for criterion in pending_item.unchecked_criteria:
                    logger.log(
                        "Pending criterion: "
                        f"line={criterion.line_number} text={criterion.text}",
                    )

            head_before = git_output_or_fail(
                git_bin=git_bin,
                cwd=repo_root,
                logger=logger,
                label=f"git.head.before.cycle{cycle_number}",
                args=["rev-parse", "HEAD"],
            )

            implementation_prompt = build_implementation_prompt(
                repo_root=repo_root,
                plan_path=plan_path,
                design_path=design_path,
                item=pending_item,
            )
            logger.log(
                "Implementation prompt prepared and saved to per-step artifact file.",
            )
            implement_result = run_codex_step(
                codex_bin=codex_bin,
                repo_root=repo_root,
                model=args.model,
                cycle_number=cycle_number,
                step_name="implement",
                prompt=implementation_prompt,
                logs_root=logs_root,
                logger=logger,
            )
            if implement_result.returncode != 0:
                quota_hit = detect_quota_issue(
                    implement_result.output,
                    implement_result.last_message,
                )
                if quota_hit is not None:
                    if wait_for_quota_reset(
                        codex_bin=codex_bin,
                        repo_root=repo_root,
                        model=args.model,
                        logger=logger,
                        interval=args.quota_wait_interval,
                        max_attempts=args.max_quota_waits,
                        step_name="implement",
                        matched_line=quota_hit,
                    ):
                        continue
                    return graceful_quota_exit(
                        logger=logger,
                        step_name="implement",
                        matched_line=quota_hit,
                    )
                logger.log(
                    "ERROR: implement step failed with code "
                    f"{implement_result.returncode}.",
                )
                return implement_result.returncode

            if is_plan_done(implement_result.last_message):
                logger.log("Codex returned PLAN IS DONE.")
                if args.docs_review_interval > 0:
                    run_docs_review(
                        codex_bin=codex_bin,
                        repo_root=repo_root,
                        model=args.model,
                        cycle_number=cycle_number,
                        logs_root=logs_root,
                        logger=logger,
                        quota_wait_interval=args.quota_wait_interval,
                        max_quota_waits=args.max_quota_waits,
                    )
                logger.log("Stopping automation loop.")
                _ = sys.stdout.write("PLAN IS DONE\n")
                return 0

            head_after_implement = git_output_or_fail(
                git_bin=git_bin,
                cwd=repo_root,
                logger=logger,
                label=f"git.head.after_implement.cycle{cycle_number}",
                args=["rev-parse", "HEAD"],
            )
            if head_before == head_after_implement:
                quota_hit = detect_quota_issue(
                    implement_result.output,
                    implement_result.last_message,
                )
                if quota_hit is not None:
                    if wait_for_quota_reset(
                        codex_bin=codex_bin,
                        repo_root=repo_root,
                        model=args.model,
                        logger=logger,
                        interval=args.quota_wait_interval,
                        max_attempts=args.max_quota_waits,
                        step_name="implement",
                        matched_line=quota_hit,
                    ):
                        continue
                    return graceful_quota_exit(
                        logger=logger,
                        step_name="implement",
                        matched_line=quota_hit,
                    )
                logger.log(
                    "ERROR: implement step finished without advancing HEAD and "
                    "without PLAN IS DONE sentinel.",
                )
                return 1

            commit_count_text = git_output_or_fail(
                git_bin=git_bin,
                cwd=repo_root,
                logger=logger,
                label=f"git.commit_count.implement.cycle{cycle_number}",
                args=["rev-list", "--count", f"{head_before}..{head_after_implement}"],
            )
            logger.log(
                "Implementation step advanced HEAD by "
                f"{commit_count_text} commit(s). "
                f"target_commit={head_after_implement}",
            )
            _ = run_capture(
                command=[git_bin, "log", "--oneline", "--decorate", "-n", "3"],
                cwd=repo_root,
                logger=logger,
                label=f"git.log.after_implement.cycle{cycle_number}",
            )

            review_prompt = build_review_prompt(
                repo_root=repo_root,
                plan_path=plan_path,
                implemented_item=pending_item,
                implemented_commit=head_after_implement,
                branch=branch,
                upstream=upstream,
            )
            logger.log("Review prompt prepared and saved to per-step artifact file.")
            while True:
                review_result = run_codex_step(
                    codex_bin=codex_bin,
                    repo_root=repo_root,
                    model=args.model,
                    cycle_number=cycle_number,
                    step_name="review_fix_push",
                    prompt=review_prompt,
                    logs_root=logs_root,
                    logger=logger,
                )
                if review_result.returncode == 0:
                    break
                quota_hit = detect_quota_issue(
                    review_result.output,
                    review_result.last_message,
                )
                if quota_hit is not None:
                    if wait_for_quota_reset(
                        codex_bin=codex_bin,
                        repo_root=repo_root,
                        model=args.model,
                        logger=logger,
                        interval=args.quota_wait_interval,
                        max_attempts=args.max_quota_waits,
                        step_name="review_fix_push",
                        matched_line=quota_hit,
                    ):
                        continue
                    return graceful_quota_exit(
                        logger=logger,
                        step_name="review_fix_push",
                        matched_line=quota_hit,
                    )
                logger.log(
                    f"ERROR: review/fix/push step failed with code "
                    f"{review_result.returncode}.",
                )
                return review_result.returncode

            head_after_review = git_output_or_fail(
                git_bin=git_bin,
                cwd=repo_root,
                logger=logger,
                label=f"git.head.after_review.cycle{cycle_number}",
                args=["rev-parse", "HEAD"],
            )
            if head_after_review != head_after_implement:
                logger.log(
                    "Review step created follow-up commit. "
                    f"new_head={head_after_review}",
                )
            else:
                logger.log("Review step did not create additional commit.")

            _ = run_capture(
                command=[git_bin, "status", "--short", "--branch"],
                cwd=repo_root,
                logger=logger,
                label=f"git.status.after_cycle{cycle_number}",
            )
            if upstream is not None:
                divergence = maybe_git_output(
                    git_bin=git_bin,
                    cwd=repo_root,
                    logger=logger,
                    label=f"git.divergence.after_cycle{cycle_number}",
                    args=["rev-list", "--left-right", "--count", f"HEAD...{upstream}"],
                )
                if divergence is not None:
                    logger.log(
                        "Branch divergence after cycle "
                        f"{cycle_number}: HEAD...{upstream} => {divergence}",
                    )
                else:
                    logger.log(
                        "Could not compute branch divergence after cycle; "
                        "review prompt still attempted push.",
                    )
            else:
                logger.log(
                    "No upstream configured; push attempt outcome depends on "
                    "prompt logic.",
                )

            if (
                args.docs_review_interval > 0
                and cycle_number % args.docs_review_interval == 0
            ):
                run_docs_review(
                    codex_bin=codex_bin,
                    repo_root=repo_root,
                    model=args.model,
                    cycle_number=cycle_number,
                    logs_root=logs_root,
                    logger=logger,
                    quota_wait_interval=args.quota_wait_interval,
                    max_quota_waits=args.max_quota_waits,
                )

            if args.sleep_seconds > 0:
                logger.log(
                    "Sleeping for "
                    f"{args.sleep_seconds:.2f} second(s) before next cycle.",
                )
                time.sleep(args.sleep_seconds)

        logger.log(
            f"Reached max cycles ({args.max_cycles}) without PLAN IS DONE sentinel.",
        )
        return 2  # noqa: TRY300
    except KeyboardInterrupt:
        logger.log("Interrupted by user.")
        return 130
    except Exception as exc:  # noqa: BLE001
        logger.log(f"Unhandled error: {exc}")
        return 1
    finally:
        logger.log("Runner exiting.")
        logger.close()


if __name__ == "__main__":
    raise SystemExit(main())
