#!/usr/bin/env python3
"""Verbose automation loop for implementation-plan execution via Codex CLI."""

from __future__ import annotations

import argparse
import json
import re
import secrets
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
RETRYABLE_RATE_LIMIT_PATTERNS = (
    re.compile(r"\b429\b", re.IGNORECASE),
    re.compile(r"\btoo many requests\b", re.IGNORECASE),
    re.compile(r"\brate[ -]?limit(?:ed)?\b", re.IGNORECASE),
    re.compile(r"\bexceeded\b.*\brate\b", re.IGNORECASE),
)
QUOTA_EXHAUSTION_PATTERNS = (
    re.compile(r"\binsufficient[_ ]quota\b", re.IGNORECASE),
    re.compile(r"\busage limit\b", re.IGNORECASE),
    re.compile(r"\bquota exceeded\b", re.IGNORECASE),
    re.compile(r"\bexceeded\b.*\b(quota|usage)\b", re.IGNORECASE),
    re.compile(r"\byou(?:'ve| have) reached .*limit\b", re.IGNORECASE),
    re.compile(r"\bout of credits?\b", re.IGNORECASE),
)
FORBIDDEN_GIT_NO_VERIFY_PATTERN = re.compile(
    r"\bgit\b[^\n\r]*\s--no-verify(?:\s|$)",
    re.IGNORECASE,
)
CODEX_OPTION_5_MIN_VERSION = (0, 102, 0)
ASK_FOR_APPROVAL_FLAG = "--ask-for-approval"


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


@dataclass(frozen=True)
class CodexCliCapabilities:
    """Detected Codex CLI capabilities relevant for automation settings."""

    version_text: str
    parsed_version: tuple[int, int, int] | None
    supports_ask_for_approval_flag: bool
    enable_option_5: bool
    option_5_warning: str | None


@dataclass(frozen=True)
class RateLimitIssue:
    """Matched rate-limit category extracted from tool output."""

    line: str
    retryable: bool


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


def parse_codex_version(version_output: str) -> tuple[int, int, int] | None:
    """Extract semantic version from `codex --version` output."""
    match = re.search(r"\bcodex-cli\s+(\d+)\.(\d+)\.(\d+)\b", version_output)
    if match is None:
        return None
    major, minor, patch = match.groups()
    return (int(major), int(minor), int(patch))


def version_to_text(version: tuple[int, int, int] | None) -> str:
    """Render semantic version tuple for logs."""
    if version is None:
        return "unknown"
    return ".".join(str(part) for part in version)


def detect_codex_capabilities(
    *,
    codex_bin: str,
    repo_root: Path,
    logger: TeeLogger,
) -> CodexCliCapabilities:
    """Probe Codex CLI support for option 5 and return an enable/disable decision."""
    version_result = run_capture(
        command=[codex_bin, "--version"],
        cwd=repo_root,
        logger=logger,
        label="codex.version",
    )
    help_result = run_capture(
        command=[codex_bin, "--help"],
        cwd=repo_root,
        logger=logger,
        label="codex.help",
    )
    parsed_version = parse_codex_version(version_result.output)
    supports_ask_for_approval_flag = ASK_FOR_APPROVAL_FLAG in help_result.output

    warning: str | None = None
    enable_option_5 = True
    if parsed_version is None:
        warning = (
            "WARNING: Could not parse Codex CLI version. "
            "Disabling option 5 and continuing with options 1 and 2 only."
        )
        enable_option_5 = False
    elif parsed_version < CODEX_OPTION_5_MIN_VERSION:
        warning = (
            "WARNING: Codex CLI version is below required minimum for option 5 "
            f"(found {version_to_text(parsed_version)}, "
            f"required >= {version_to_text(CODEX_OPTION_5_MIN_VERSION)}). "
            "Continuing with options 1 and 2 only."
        )
        enable_option_5 = False
    elif not supports_ask_for_approval_flag:
        warning = (
            "WARNING: Codex CLI does not advertise --ask-for-approval support. "
            "Disabling option 5 and continuing with options 1 and 2 only."
        )
        enable_option_5 = False

    return CodexCliCapabilities(
        version_text=version_to_text(parsed_version),
        parsed_version=parsed_version,
        supports_ask_for_approval_flag=supports_ask_for_approval_flag,
        enable_option_5=enable_option_5,
        option_5_warning=warning,
    )


def is_plan_done(last_message: str) -> bool:
    """Return True when codex replied with PLAN IS DONE sentinel."""
    try:
        payload = json.loads(last_message)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict) and payload.get("STATUS") == "PLAN_IS_DONE":
        return True
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


def detect_rate_limit_issue(*texts: str) -> RateLimitIssue | None:
    """Classify rate-limit output as retryable (429) or hard quota exhaustion."""
    for text in texts:
        for line in text.splitlines():
            stripped = line.strip()
            for pattern in RETRYABLE_RATE_LIMIT_PATTERNS:
                if pattern.search(line):
                    return RateLimitIssue(line=stripped, retryable=True)
            for pattern in QUOTA_EXHAUSTION_PATTERNS:
                if pattern.search(line):
                    return RateLimitIssue(line=stripped, retryable=False)
    return None


def detect_forbidden_no_verify_usage(*texts: str) -> str | None:
    """Return the first output line that suggests forbidden `git ... --no-verify`."""
    for text in texts:
        for line in text.splitlines():
            if FORBIDDEN_GIT_NO_VERIFY_PATTERN.search(line):
                return line.strip()
    return None


def compute_full_jitter_backoff_seconds(
    *,
    attempt: int,
    base_seconds: float,
    max_seconds: float,
) -> float:
    """Return a full-jitter exponential backoff delay."""
    if attempt <= 0 or base_seconds <= 0.0 or max_seconds <= 0.0:
        return 0.0
    upper_bound = min(max_seconds, base_seconds * (2 ** (attempt - 1)))
    if upper_bound <= 0.0:
        return 0.0
    # Use a cryptographic RNG to satisfy strict linting.
    jitter_fraction = float(secrets.randbelow(1_000_000)) / 1_000_000
    return jitter_fraction * upper_bound


def wait_for_retryable_rate_limit(  # noqa: PLR0913
    *,
    logger: TeeLogger,
    step_name: str,
    matched_line: str,
    attempt: int,
    max_attempts: int,
    backoff_base_seconds: float,
    backoff_max_seconds: float,
) -> bool:
    """Sleep with full-jitter backoff for retryable 429/rate-limit responses."""
    if attempt > max_attempts:
        return False
    delay_seconds = compute_full_jitter_backoff_seconds(
        attempt=attempt,
        base_seconds=backoff_base_seconds,
        max_seconds=backoff_max_seconds,
    )
    logger.log(
        "Retryable rate limit detected during "
        f"'{step_name}' (attempt {attempt}/{max_attempts}). "
        f"Matched: {matched_line}",
    )
    logger.log(
        "Sleeping with full-jitter exponential backoff for "
        f"{delay_seconds:.2f}s before retry.",
    )
    if delay_seconds > 0.0:
        time.sleep(delay_seconds)
    return True


def build_output_schema(step_name: str) -> dict[str, object]:
    """Build strict JSON Schema for the step's final response."""
    sha_or_none = r"^(?:[0-9a-f]{7,40}|NONE)$"
    common_required = [
        "QUESTIONS_ASKED",
        "SAFE_DEFAULT_DECISIONS",
        "NO_VERIFY_USED",
        "PRECOMMIT_ALL_FILES_STATUS",
    ]
    common_fields: dict[str, object] = {
        "QUESTIONS_ASKED": {"type": "integer", "enum": [0]},
        "SAFE_DEFAULT_DECISIONS": {
            "type": "array",
            "items": {"type": "string"},
        },
        "NO_VERIFY_USED": {"type": "boolean", "enum": [False]},
        "PRECOMMIT_ALL_FILES_STATUS": {"type": "string", "enum": ["PASSED"]},
    }

    if step_name == "implement":
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "STATUS",
                "IMPLEMENTED_ITEM",
                "IMPLEMENTED_TITLE",
                "IMPLEMENTATION_COMMIT",
                "IMPLEMENTATION_SUMMARY",
                *common_required,
            ],
            "properties": {
                "STATUS": {"type": "string", "enum": ["PLAN_IS_DONE", "IMPLEMENTED"]},
                "IMPLEMENTED_ITEM": {"type": "string", "minLength": 1},
                "IMPLEMENTED_TITLE": {"type": "string", "minLength": 1},
                "IMPLEMENTATION_COMMIT": {"type": "string", "pattern": sha_or_none},
                "IMPLEMENTATION_SUMMARY": {"type": "string", "minLength": 1},
                **common_fields,
            },
        }

    if step_name == "review_fix_push":
        covered_or_na: dict[str, object] = {
            "type": "string",
            "enum": ["covered", "n/a"],
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "REVIEW_TARGET_COMMIT",
                "REVIEW_FINDINGS_FIXED",
                "REVIEW_FIX_COMMIT",
                "PUSH_STATUS",
                "REVIEW_CHECKLIST",
                *common_required,
            ],
            "properties": {
                "REVIEW_TARGET_COMMIT": {"type": "string", "minLength": 7},
                "REVIEW_FINDINGS_FIXED": {"type": "integer", "minimum": 0},
                "REVIEW_FIX_COMMIT": {"type": "string", "pattern": sha_or_none},
                "PUSH_STATUS": {"type": "string", "minLength": 2},
                "REVIEW_CHECKLIST": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "security",
                        "test_isolation",
                        "migration_coverage",
                        "exception_handling",
                        "field_validation",
                        "traceability_sha",
                    ],
                    "properties": {
                        "security": covered_or_na,
                        "test_isolation": covered_or_na,
                        "migration_coverage": covered_or_na,
                        "exception_handling": covered_or_na,
                        "field_validation": covered_or_na,
                        "traceability_sha": {
                            "type": "string",
                            "enum": ["verified"],
                        },
                    },
                },
                **common_fields,
            },
        }

    if step_name == "docs_review":
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "DOCS_REVIEW_FILES_CHANGED",
                "DOCS_REVIEW_COMMIT",
                "PUSH_STATUS",
                *common_required,
            ],
            "properties": {
                "DOCS_REVIEW_FILES_CHANGED": {"type": "integer", "minimum": 0},
                "DOCS_REVIEW_COMMIT": {"type": "string", "pattern": sha_or_none},
                "PUSH_STATUS": {"type": "string", "minLength": 2},
                **common_fields,
            },
        }

    if step_name == "precommit_repair":
        return {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "PRECOMMIT_REPAIR_COMMIT",
                "PUSH_STATUS",
                *common_required,
            ],
            "properties": {
                "PRECOMMIT_REPAIR_COMMIT": {"type": "string", "pattern": sha_or_none},
                "PUSH_STATUS": {"type": "string", "minLength": 2},
                **common_fields,
            },
        }

    message = f"Unsupported step name for output schema: {step_name}"
    raise ValueError(message)


def build_codex_exec_command(
    *,
    codex_bin: str,
    repo_root: Path,
    model: str | None,
    explicit_never_approval: bool,
) -> list[str]:
    """Create baseline codex exec command with optional explicit approval policy."""
    command: list[str] = [codex_bin]
    if explicit_never_approval:
        command.extend(["-a", "never"])
    command.extend(
        [
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--cd",
            str(repo_root),
        ],
    )
    if model is not None:
        command.extend(["--model", model])
    return command


def maybe_apply_codex_exec_cooldown(
    *,
    logger: TeeLogger,
    step_name: str,
    cooldown_seconds: float,
) -> None:
    """Apply optional fixed delay before each codex exec invocation."""
    if cooldown_seconds <= 0.0:
        return
    logger.log(
        "Applying codex exec cooldown before "
        f"'{step_name}': sleeping {cooldown_seconds:.2f}s.",
    )
    time.sleep(cooldown_seconds)


def run_quota_probe(
    *,
    codex_bin: str,
    repo_root: Path,
    model: str | None,
    logger: TeeLogger,
    explicit_never_approval: bool,
) -> bool:
    """Run a minimal codex exec to test whether API limits have reset."""
    command = build_codex_exec_command(
        codex_bin=codex_bin,
        repo_root=repo_root,
        model=model,
        explicit_never_approval=explicit_never_approval,
    )
    command.append("-")
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
    explicit_never_approval: bool,
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
            explicit_never_approval=explicit_never_approval,
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
            2. If no pending criteria remain, return STATUS=PLAN_IS_DONE in JSON.
            3. If you find pending criteria, implement exactly the first pending
               plan item,
               update plan traceability, run relevant checks, and commit it.

            Hard constraints:
            - Follow commit-atomic behavior from {plan_path}.
            - Follow architecture constraints from {design_path}.
            - Do not batch multiple plan items.
            - Do not push in this step.
            - Never ask the user any question.
            - Never request clarification or approval.
            - Resolve ambiguity autonomously using safe defaults that keep progress:
              choose the smallest reversible low-risk change that unblocks next work.
            - If unrelated dirty files exist at start, leave them untouched unless
              pre-commit requires fixing them.
            - You may include unrelated files in a commit only when required to make
              `uv run pre-commit run --all-files` pass.
            - Strictly forbidden: using --no-verify in any git command.
            - Do not bypass hooks or disable checks.
            - Run `uv run pre-commit run --all-files` before any commit.
            - If pre-commit reports issues, fix ALL reported issues (including files
              unrelated to the current item) and rerun pre-commit until it passes.
            - Return only a strict JSON object matching the output schema.

            Required JSON fields:
            - STATUS: PLAN_IS_DONE
            - IMPLEMENTED_ITEM: NONE
            - IMPLEMENTED_TITLE: NONE
            - IMPLEMENTATION_COMMIT: NONE
            - IMPLEMENTATION_SUMMARY: short reason no work remains
            - QUESTIONS_ASKED: must be 0
            - SAFE_DEFAULT_DECISIONS: list of defaults used (empty list if none)
            - NO_VERIFY_USED: false
            - PRECOMMIT_ALL_FILES_STATUS: PASSED
            """,
            ).strip()
            + "\n"
        )

    unchecked = format_criteria(item.unchecked_criteria)
    verification_commands = format_verification_commands(item.verification_commands)
    template = textwrap.dedent(
        """
        You are running in repository: __REPO_ROOT__

        Primary objective:
        Implement exactly one commit-atomic item from __PLAN_PATH__.

        Target item (already chosen):
        - ID: __ITEM_ID__
        - Title: __ITEM_TITLE__
        - Header line: __ITEM_HEADER_LINE__

        Unchecked acceptance criteria for target item:
        __UNCHECKED_CRITERIA__

        Verification commands listed in the target item:
        __VERIFICATION_COMMANDS__

        Hard constraints:
        1. Read __PLAN_PATH__ and __DESIGN_PATH__.
        2. Implement ONLY __ITEM_ID__. Do not start any later plan item.
        3. Update the target item criteria to [x] with explicit
           [Tests: tests/...::test_...]
           mappings on completed criteria.
        4. Update the target item Execution record
           (date, commit hash, verification summary).
        5. Run relevant tests/checks and use item verification commands as baseline.
        6. Keep changes focused and commit-atomic.
        7. Create exactly one implementation commit.
        8. Do NOT push in this step.
        9. If you discover no pending work exists, set STATUS=PLAN_IS_DONE.
        10. Subjective Insights: If you encounter architectural thoughts, possible
            improvements, proposals, or issues during implementation, append them
            to `INSIGHTS.md` (create it if missing). Check existing content to
            ensure no duplicates. Use a '## [Date] - [Item ID]' header.
        11. Never ask the user any question.
        12. Never request clarification or approval.
        13. Resolve ambiguity autonomously using safe defaults that keep progress:
            choose the smallest reversible low-risk change that unblocks next work.
        14. If unrelated dirty files exist at start, leave them untouched unless
            pre-commit requires fixing them.
        15. You may include unrelated files in a commit only when required to make
            `uv run pre-commit run --all-files` pass.
        16. Strictly forbidden: using --no-verify in any git command.
        17. Do not bypass hooks or disable checks.
        18. Run `uv run pre-commit run --all-files` before any commit.
        19. If pre-commit reports issues, fix ALL reported issues (including files
            unrelated to the current item) and rerun pre-commit until it passes.
        20. Return only a strict JSON object matching the output schema.

        Required JSON fields:
        - STATUS: PLAN_IS_DONE or IMPLEMENTED
        - IMPLEMENTED_ITEM: __ITEM_ID__ or NONE
        - IMPLEMENTED_TITLE: __ITEM_TITLE__ or NONE
        - IMPLEMENTATION_COMMIT: <sha|NONE>
        - IMPLEMENTATION_SUMMARY: <short summary>
        - QUESTIONS_ASKED: must be 0
        - SAFE_DEFAULT_DECISIONS: list of defaults used (empty list if none)
        - NO_VERIFY_USED: false
        - PRECOMMIT_ALL_FILES_STATUS: PASSED
        """,
    ).strip()
    return (
        template.replace("__REPO_ROOT__", str(repo_root))
        .replace("__PLAN_PATH__", str(plan_path))
        .replace("__DESIGN_PATH__", str(design_path))
        .replace("__ITEM_ID__", item.identifier)
        .replace("__ITEM_TITLE__", item.title)
        .replace("__ITEM_HEADER_LINE__", str(item.header_line))
        .replace("__UNCHECKED_CRITERIA__", unchecked)
        .replace("__VERIFICATION_COMMANDS__", verification_commands)
        + "\n"
    )


def sync_traceability_sha(
    *,
    plan_path: Path,
    item_id: str,
    actual_sha: str,
    logger: TeeLogger,
) -> bool:
    """Find target item in plan and ensure its Commit: field matches actual_sha.

    Returns True if modification was made.
    """
    if not plan_path.exists():
        return False

    content = plan_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = _update_item_sha(lines, item_id, actual_sha, logger)

    if new_lines == lines:
        return False

    plan_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True


def _update_item_sha(
    lines: list[str],
    item_id: str,
    actual_sha: str,
    logger: TeeLogger,
) -> list[str]:
    """Search for item_id block and update its Commit: line."""
    short_sha = actual_sha[:7]
    item_header_pattern = re.compile(rf"^###\s+{item_id}\s*-\s*.+$")
    execution_record_header_pattern = re.compile(r"^\s*-\s*Execution record:\s*$")
    commit_line_pattern = re.compile(r"^(\s+-\s+Commit:\s+`)([^`]+)(`.*)$")

    new_lines = list(lines)
    in_target_item = False
    in_execution_record = False

    for i, line in enumerate(new_lines):
        if item_header_pattern.match(line):
            in_target_item = True
            in_execution_record = False
            continue

        if in_target_item and line.startswith("### "):
            # Reached next item
            break

        if in_target_item and execution_record_header_pattern.match(line):
            in_execution_record = True
            continue

        if in_execution_record:
            match = commit_line_pattern.match(line)
            if match:
                prefix, old_sha, suffix = match.groups()
                if old_sha not in (short_sha, actual_sha):
                    logger.log(
                        f"Syncing traceability: {item_id} "
                        f"Commit: {old_sha} -> {short_sha}",
                    )
                    new_lines[i] = f"{prefix}{short_sha}{suffix}"
                return new_lines

    return new_lines


def run_verification_commands(
    *,
    commands: tuple[str, ...],
    cwd: Path,
    logger: TeeLogger,
) -> str:
    """Run all verification commands and return a combined output report."""
    if not commands:
        return "No verification commands defined for this item."

    report = []
    for cmd in commands:
        logger.log(f"Running verification: {cmd}")
        result = run_stream(
            command=["bash", "-c", cmd],
            cwd=cwd,
            logger=logger,
            label="verification",
        )
        status = (
            "PASSED" if result.returncode == 0 else f"FAILED (code {result.returncode})"
        )
        report.append(
            f"Command: {cmd}\nStatus: {status}\nOutput:\n{result.output}\n{'-' * 40}",
        )

    return "\n".join(report)


def build_review_prompt(  # noqa: PLR0913
    *,
    repo_root: Path,
    plan_path: Path,
    implemented_item: PlanItem | None,
    implemented_commit: str,
    branch: str,
    upstream: str | None,
    verification_report: str | None = None,
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

        7. Subjective Insights: If you encounter architectural thoughts, possible
           improvements, proposals, or issues during review, append them
           to `INSIGHTS.md` (create it if missing). Check existing content to
           ensure no duplicates. Use a '## [Date] - [Item ID]' header.

        Constraints:
        - Do not amend, rebase, or rewrite history.
        - Keep follow-up changes scoped to review findings.
        - If there are no findings that require code changes, still push.
        - Never ask the user any question.
        - Never request clarification or approval.
        - Resolve ambiguity autonomously using safest unblocking defaults.
        - If unrelated dirty files exist at start, leave them untouched unless
          pre-commit requires fixing them.
        - You may include unrelated files in a commit only when required to make
          `uv run pre-commit run --all-files` pass.
        - Strictly forbidden: using --no-verify in any git command.
        - Do not bypass hooks or disable checks.
        - Run `uv run pre-commit run --all-files` before any commit.
        - If pre-commit reports issues, fix ALL reported issues (including files
          unrelated to the reviewed commit) and rerun pre-commit until it passes.
        - Return only a strict JSON object matching the output schema.

        Domain-specific review sub-checklists:
        Work through each checklist below. Set the corresponding REVIEW_CHECKLIST
        field to "covered" once you have verified or fixed the items, or "n/a" if
        none of the touched files are in scope for that domain.

        SECURITY (set "covered" or "n/a"):
        - Any file written to disk that contains secrets or tokens: verify
          .chmod(0o600) is called immediately after creation.
        - Atomic write operations: if a side-effect (DB write, file write) partially
          succeeds and then fails, verify the partial result is rolled back.
        - Type coercion: verify bool is excluded before int in isinstance() checks
          (bool is a subclass of int — `isinstance(True, int)` is True).
        - asyncio.CancelledError: verify it is never caught inside a broad
          `except Exception` or a tuple without being explicitly re-raised.
        - Cryptographic inputs: verify key lengths, nonce lengths, and version
          fields are validated before use; reject booleans in version fields.

        TEST ISOLATION (set "covered" or "n/a"):
        - Any test that calls create_app() or triggers DB startup: verify it sets
          TCA_DB_PATH to a per-test tmp_path via monkeypatch.setenv.
        - Any datetime/date constructor in test code: verify it uses
          datetime.now(timezone.utc) + timedelta(...) instead of a hardcoded
          year/month/day that will become stale over time.
        - Any create-or-update operation: verify an idempotency test exists
          (calling the operation twice produces the same result, not an error).
        - New exception paths: verify there is a test that exercises the new branch.

        MIGRATION COVERAGE (set "covered" or "n/a" if no migrations touched):
        - Every upgrade() must have a corresponding downgrade test that confirms
          all created tables/indexes/columns are removed on downgrade.
        - Tables with natural compound keys: verify a UNIQUE constraint is present.
        - String columns that act as enums: verify a CHECK constraint is present.
        - FTS content tables: if the migration creates triggers but pre-existing
          rows exist, verify a rebuild/backfill step is included.

        API LAYER (set "covered" or "n/a" if no api/routes/ files touched):
        - Pydantic string fields where empty is invalid: verify min_length=1 is set.
        - POST/PUT handlers that create or update a record: verify the handler uses
          the return value of the write call directly (no post-write re-read TOCTOU).
        - Any new or modified endpoint: verify bearer auth test coverage exists.

        EXCEPTION HANDLING (set "covered" or "n/a"):
        - IntegrityError handling in repositories: verify the code inspects the
          error message to distinguish duplicate-key errors from other constraint
          violations before remapping to a domain exception.
        - Duck-typed interface checks: verify callable(getattr(obj, "method", None))
          is used instead of hasattr(obj, "method").
        - Lifespan/startup hooks: verify each dependency is appended to a
          started-list only after startup() succeeds, and shutdown iterates only
          the started list.

        FIELD VALIDATION (set "covered" or "n/a"):
        - Pydantic model fields that accept user strings: verify min/max length
          constraints are present where empty or oversized values are invalid.
        - JSON value storage: verify non-finite floats (inf, nan) and boolean
          values masquerading as integers are rejected at the storage boundary.

        TRACEABILITY (traceability_sha must always be "verified"):
        - The Execution record Commit: field in the plan for this item must contain
          the real SHA of the implementation commit, not a placeholder (PENDING,
          NONE, a plan-item code like C056, or a truncated/incorrect hash).
          The runner has detected the implementation commit is: {implemented_commit}
          Verify this SHA is recorded correctly in {plan_path} for {item_label}.
        - The Execution record block must be positioned after the acceptance
          criteria checklist, not before it.

        Verification Results from Runner (Objective State):
        {verification_report or "No automated verification was executed by the runner."}

        Context files:
        - {plan_path}

        Required JSON fields:
        - REVIEW_TARGET_COMMIT: {implemented_commit}
        - REVIEW_FINDINGS_FIXED: <integer>
        - REVIEW_FIX_COMMIT: <sha|NONE>
        - PUSH_STATUS: <OK|FAILED: reason>
        - REVIEW_CHECKLIST:
            security: "covered" | "n/a"
            test_isolation: "covered" | "n/a"
            migration_coverage: "covered" | "n/a"
            exception_handling: "covered" | "n/a"
            field_validation: "covered" | "n/a"
            traceability_sha: "verified"  (always required, never n/a)
        - QUESTIONS_ASKED: must be 0
        - SAFE_DEFAULT_DECISIONS: list of defaults used (empty list if none)
        - NO_VERIFY_USED: false
        - PRECOMMIT_ALL_FILES_STATUS: PASSED
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
        - Keep changes docs-focused unless pre-commit requires fixes in other files.
        - If all docs are already accurate, make no changes and push nothing.
        - Never ask the user any question.
        - Never request clarification or approval.
        - Resolve ambiguity autonomously using safe defaults.
        - Strictly forbidden: using --no-verify in any git command.
        - Do not bypass hooks or disable checks.
        - Run `uv run pre-commit run --all-files` before any commit.
        - If pre-commit reports issues, fix ALL reported issues (including files
          unrelated to the docs scope) and rerun pre-commit until it passes.
        - Return only a strict JSON object matching the output schema.

        Required JSON fields:
        - DOCS_REVIEW_FILES_CHANGED: <integer>
        - DOCS_REVIEW_COMMIT: <sha|NONE>
        - PUSH_STATUS: <OK|FAILED: reason|SKIPPED>
        - QUESTIONS_ASKED: must be 0
        - SAFE_DEFAULT_DECISIONS: list of defaults used (empty list if none)
        - NO_VERIFY_USED: false
        - PRECOMMIT_ALL_FILES_STATUS: PASSED
        """,
        ).strip()
        + "\n"
    )


def truncate_precommit_output_for_prompt(
    *,
    output: str,
    max_lines: int = 200,
    max_chars: int = 12_000,
) -> str:
    """Trim pre-commit output to a compact excerpt suitable for prompt context."""
    lines = output.splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    excerpt = "\n".join(lines).strip()
    if len(excerpt) > max_chars:
        excerpt = excerpt[-max_chars:]
    if not excerpt:
        return "(no pre-commit output captured)"
    return excerpt


def build_precommit_repair_prompt(
    *,
    repo_root: Path,
    precommit_output_excerpt: str,
) -> str:
    """Create a strict remediation prompt for repository-wide pre-commit failures."""
    return (
        textwrap.dedent(
            f"""
        You are running in repository: {repo_root}

        Objective:
        Make `uv run pre-commit run --all-files` pass for the entire repository.

        Latest pre-commit output excerpt:
        {precommit_output_excerpt}

        Required workflow:
        1. Run `uv run pre-commit run --all-files`.
        2. Fix every reported issue across all files.
        3. Re-run `uv run pre-commit run --all-files`.
        4. Repeat until it exits with code 0.
        5. Commit the required fixes in one commit (if changes exist).
        6. Push current branch to its upstream if a commit was created.

        Hard constraints:
        - Never ask the user any question.
        - Never request clarification or approval.
        - Resolve ambiguity autonomously using the safest reversible default.
        - Strictly forbidden: using --no-verify in any git command.
        - Do not bypass hooks or disable checks.
        - Return only a strict JSON object matching the output schema.

        Required JSON fields:
        - PRECOMMIT_REPAIR_COMMIT: <sha|NONE>
        - PUSH_STATUS: <OK|FAILED: reason|SKIPPED>
        - QUESTIONS_ASKED: must be 0
        - SAFE_DEFAULT_DECISIONS: list of defaults used (empty list if none)
        - NO_VERIFY_USED: false
        - PRECOMMIT_ALL_FILES_STATUS: PASSED
        """,
        ).strip()
        + "\n"
    )


def enforce_no_verify_policy_or_fail(
    *,
    logger: TeeLogger,
    step_name: str,
    output: str,
    last_message: str,
) -> bool:
    """Return False when output suggests forbidden `git ... --no-verify` usage."""
    matched_line = detect_forbidden_no_verify_usage(output, last_message)
    if matched_line is None:
        return True
    logger.log(
        "ERROR: forbidden '--no-verify' usage detected during "
        f"'{step_name}'. Matched line: {matched_line}",
    )
    return False


def enforce_precommit_policy(  # noqa: C901, PLR0911, PLR0913
    *,
    uv_bin: str,
    codex_bin: str,
    repo_root: Path,
    model: str | None,
    cycle_number: int,
    logs_root: Path,
    logger: TeeLogger,
    codex_exec_cooldown_seconds: float,
    retryable_rate_limit_max_retries: int,
    retryable_rate_limit_backoff_base_seconds: float,
    retryable_rate_limit_backoff_max_seconds: float,
    quota_wait_interval: float,
    max_quota_waits: int,
    explicit_never_approval: bool,
    precommit_repair_max_attempts: int,
) -> int | None:
    """Enforce repo-wide pre-commit pass with autonomous Codex repair attempts."""
    for check_attempt in range(1, precommit_repair_max_attempts + 1):
        precommit_result = run_stream(
            command=[uv_bin, "run", "pre-commit", "run", "--all-files"],
            cwd=repo_root,
            stdin_text=None,
            logger=logger,
            label=f"precommit.all_files.attempt{check_attempt}",
        )
        if precommit_result.returncode == 0:
            logger.log("Policy gate passed: pre-commit all-files is clean.")
            return None

        logger.log(
            "Policy gate failed: pre-commit reported repository-wide issues "
            f"(attempt {check_attempt}/{precommit_repair_max_attempts}).",
        )
        if check_attempt == precommit_repair_max_attempts:
            logger.log(
                "ERROR: pre-commit policy enforcement exhausted repair attempts.",
            )
            return 1

        repair_prompt = build_precommit_repair_prompt(
            repo_root=repo_root,
            precommit_output_excerpt=truncate_precommit_output_for_prompt(
                output=precommit_result.output,
            ),
        )
        repair_retryable_attempt = 0
        while True:
            repair_result = run_codex_step(
                codex_bin=codex_bin,
                repo_root=repo_root,
                model=model,
                cycle_number=cycle_number,
                step_name="precommit_repair",
                prompt=repair_prompt,
                logs_root=logs_root,
                logger=logger,
                codex_exec_cooldown_seconds=codex_exec_cooldown_seconds,
                explicit_never_approval=explicit_never_approval,
            )
            if not enforce_no_verify_policy_or_fail(
                logger=logger,
                step_name="precommit_repair",
                output=repair_result.output,
                last_message=repair_result.last_message,
            ):
                return 1
            if repair_result.returncode == 0:
                logger.log(
                    "Pre-commit repair step completed; re-checking pre-commit.",
                )
                break

            rate_limit_issue = detect_rate_limit_issue(
                repair_result.output,
                repair_result.last_message,
            )
            if rate_limit_issue is not None and rate_limit_issue.retryable:
                repair_retryable_attempt += 1
                if wait_for_retryable_rate_limit(
                    logger=logger,
                    step_name="precommit_repair",
                    matched_line=rate_limit_issue.line,
                    attempt=repair_retryable_attempt,
                    max_attempts=retryable_rate_limit_max_retries,
                    backoff_base_seconds=retryable_rate_limit_backoff_base_seconds,
                    backoff_max_seconds=retryable_rate_limit_backoff_max_seconds,
                ):
                    continue
                return graceful_quota_exit(
                    logger=logger,
                    step_name="precommit_repair",
                    matched_line=rate_limit_issue.line,
                )
            if rate_limit_issue is not None:
                if wait_for_quota_reset(
                    codex_bin=codex_bin,
                    repo_root=repo_root,
                    model=model,
                    logger=logger,
                    interval=quota_wait_interval,
                    max_attempts=max_quota_waits,
                    step_name="precommit_repair",
                    matched_line=rate_limit_issue.line,
                    explicit_never_approval=explicit_never_approval,
                ):
                    continue
                return graceful_quota_exit(
                    logger=logger,
                    step_name="precommit_repair",
                    matched_line=rate_limit_issue.line,
                )
            logger.log(
                f"ERROR: precommit_repair step failed with code "
                f"{repair_result.returncode}.",
            )
            return repair_result.returncode

    logger.log("ERROR: pre-commit policy loop exited unexpectedly.")
    return 1


def run_docs_review(  # noqa: PLR0913
    *,
    codex_bin: str,
    repo_root: Path,
    model: str | None,
    cycle_number: int,
    logs_root: Path,
    logger: TeeLogger,
    codex_exec_cooldown_seconds: float,
    retryable_rate_limit_max_retries: int,
    retryable_rate_limit_backoff_base_seconds: float,
    retryable_rate_limit_backoff_max_seconds: float,
    quota_wait_interval: float,
    max_quota_waits: int,
    explicit_never_approval: bool,
) -> int | None:
    """Run a docs review step with quota retry.

    Non-critical: logs warnings on failure.
    """
    logger.section(f"Docs Review (after cycle {cycle_number})")
    prompt = build_docs_review_prompt(repo_root=repo_root)
    retryable_attempt = 0
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
            codex_exec_cooldown_seconds=codex_exec_cooldown_seconds,
            explicit_never_approval=explicit_never_approval,
        )
        if not enforce_no_verify_policy_or_fail(
            logger=logger,
            step_name="docs_review",
            output=result.output,
            last_message=result.last_message,
        ):
            logger.log("ERROR: Docs review used forbidden --no-verify.")
            return 1
        if result.returncode == 0:
            logger.log("Docs review step completed successfully.")
            return None
        rate_limit_issue = detect_rate_limit_issue(result.output, result.last_message)
        if rate_limit_issue is not None and rate_limit_issue.retryable:
            retryable_attempt += 1
            if wait_for_retryable_rate_limit(
                logger=logger,
                step_name="docs_review",
                matched_line=rate_limit_issue.line,
                attempt=retryable_attempt,
                max_attempts=retryable_rate_limit_max_retries,
                backoff_base_seconds=retryable_rate_limit_backoff_base_seconds,
                backoff_max_seconds=retryable_rate_limit_backoff_max_seconds,
            ):
                continue
            logger.log(
                "WARNING: Docs review retryable rate limit retries exhausted "
                "— skipping.",
            )
            return None
        if rate_limit_issue is not None:
            if wait_for_quota_reset(
                codex_bin=codex_bin,
                repo_root=repo_root,
                model=model,
                logger=logger,
                interval=quota_wait_interval,
                max_attempts=max_quota_waits,
                step_name="docs_review",
                matched_line=rate_limit_issue.line,
                explicit_never_approval=explicit_never_approval,
            ):
                continue
            logger.log(
                "WARNING: Docs review quota exhausted — skipping. "
                f"Matched: {rate_limit_issue.line}",
            )
            return None
        logger.log(
            f"WARNING: Docs review step failed with code {result.returncode} "
            "— skipping (non-critical).",
        )
        return None


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
    codex_exec_cooldown_seconds: float,
    explicit_never_approval: bool,
) -> CodexStepResult:
    """Run one codex exec call and persist prompt/output artifacts."""
    step_dir = logs_root / f"cycle_{cycle_number:03d}_{step_name}"
    step_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = step_dir / "prompt.txt"
    output_path = step_dir / "codex_output.log"
    last_message_path = step_dir / "last_message.txt"
    output_schema_path = step_dir / "output_schema.json"
    prompt_path.write_text(prompt, encoding="utf-8")
    output_schema_path.write_text(
        json.dumps(build_output_schema(step_name), indent=2) + "\n",
        encoding="utf-8",
    )

    command = build_codex_exec_command(
        codex_bin=codex_bin,
        repo_root=repo_root,
        model=model,
        explicit_never_approval=explicit_never_approval,
    )
    command.extend(
        [
            "--output-schema",
            str(output_schema_path),
            "--output-last-message",
            str(last_message_path),
        ],
    )
    command.append("-")
    maybe_apply_codex_exec_cooldown(
        logger=logger,
        step_name=step_name,
        cooldown_seconds=codex_exec_cooldown_seconds,
    )

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
        "--codex-exec-cooldown-seconds",
        type=float,
        default=0.0,
        help="Optional fixed delay before each codex exec request.",
    )
    parser.add_argument(
        "--precommit-repair-max-attempts",
        type=int,
        default=20,
        help=(
            "Max policy-enforcement loops for `uv run pre-commit run --all-files`. "
            "Each failed check triggers an autonomous repair step until clean."
        ),
    )
    parser.add_argument(
        "--retryable-rate-limit-max-retries",
        type=int,
        default=6,
        help="Max retries for retryable 429/rate-limit responses.",
    )
    parser.add_argument(
        "--retryable-rate-limit-backoff-base-seconds",
        type=float,
        default=2.0,
        help="Base seconds for full-jitter exponential backoff on 429 responses.",
    )
    parser.add_argument(
        "--retryable-rate-limit-backoff-max-seconds",
        type=float,
        default=60.0,
        help="Max capped seconds for full-jitter exponential backoff on 429 responses.",
    )
    parser.add_argument(
        "--quota-wait-interval",
        type=float,
        default=3600.0,
        help=(
            "Seconds between quota-reset probes for non-retryable quota exhaustion "
            "(default: 3600 = 1 hour)."
        ),
    )
    parser.add_argument(
        "--max-quota-waits",
        type=int,
        default=0,
        help=(
            "Max probe attempts before giving up on non-retryable quota exhaustion. "
            "0 = exit immediately on non-retryable quota hit (default)."
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
        logger.log(f"codex_exec_cooldown_seconds={args.codex_exec_cooldown_seconds}")
        logger.log(
            f"precommit_repair_max_attempts={args.precommit_repair_max_attempts}",
        )
        logger.log(
            "retryable_rate_limit="
            f"max_retries={args.retryable_rate_limit_max_retries}, "
            f"base={args.retryable_rate_limit_backoff_base_seconds}, "
            f"max={args.retryable_rate_limit_backoff_max_seconds}",
        )
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
        if args.codex_exec_cooldown_seconds < 0.0:
            logger.log("ERROR: --codex-exec-cooldown-seconds must be >= 0.")
            return 1
        if args.retryable_rate_limit_max_retries < 0:
            logger.log("ERROR: --retryable-rate-limit-max-retries must be >= 0.")
            return 1
        if args.retryable_rate_limit_backoff_base_seconds < 0.0:
            logger.log(
                "ERROR: --retryable-rate-limit-backoff-base-seconds must be >= 0.",
            )
            return 1
        if args.retryable_rate_limit_backoff_max_seconds < 0.0:
            logger.log(
                "ERROR: --retryable-rate-limit-backoff-max-seconds must be >= 0.",
            )
            return 1
        if args.precommit_repair_max_attempts < 1:
            logger.log("ERROR: --precommit-repair-max-attempts must be >= 1.")
            return 1

        codex_bin = resolve_executable(args.codex_bin)
        git_bin = resolve_executable(args.git_bin)
        uv_bin = resolve_executable("uv")
        logger.log(f"resolved codex_bin={codex_bin}")
        logger.log(f"resolved git_bin={git_bin}")
        logger.log(f"resolved uv_bin={uv_bin}")
        codex_capabilities = detect_codex_capabilities(
            codex_bin=codex_bin,
            repo_root=repo_root,
            logger=logger,
        )
        logger.log(
            "codex.capabilities | "
            f"version={codex_capabilities.version_text} "
            f"supports_{ASK_FOR_APPROVAL_FLAG}="
            f"{codex_capabilities.supports_ask_for_approval_flag}",
        )
        explicit_never_approval = codex_capabilities.enable_option_5
        if codex_capabilities.option_5_warning is not None:
            logger.log(codex_capabilities.option_5_warning)
        else:
            logger.log(
                "Option 5 enabled: enforcing explicit approval policy with '-a never'.",
            )

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
            implement_retryable_attempt = 0
            while True:
                implement_result = run_codex_step(
                    codex_bin=codex_bin,
                    repo_root=repo_root,
                    model=args.model,
                    cycle_number=cycle_number,
                    step_name="implement",
                    prompt=implementation_prompt,
                    logs_root=logs_root,
                    logger=logger,
                    codex_exec_cooldown_seconds=args.codex_exec_cooldown_seconds,
                    explicit_never_approval=explicit_never_approval,
                )
                if not enforce_no_verify_policy_or_fail(
                    logger=logger,
                    step_name="implement",
                    output=implement_result.output,
                    last_message=implement_result.last_message,
                ):
                    return 1
                if implement_result.returncode == 0:
                    break

                rate_limit_issue = detect_rate_limit_issue(
                    implement_result.output,
                    implement_result.last_message,
                )
                if rate_limit_issue is not None and rate_limit_issue.retryable:
                    implement_retryable_attempt += 1
                    if wait_for_retryable_rate_limit(
                        logger=logger,
                        step_name="implement",
                        matched_line=rate_limit_issue.line,
                        attempt=implement_retryable_attempt,
                        max_attempts=args.retryable_rate_limit_max_retries,
                        backoff_base_seconds=(
                            args.retryable_rate_limit_backoff_base_seconds
                        ),
                        backoff_max_seconds=args.retryable_rate_limit_backoff_max_seconds,
                    ):
                        continue
                    return graceful_quota_exit(
                        logger=logger,
                        step_name="implement",
                        matched_line=rate_limit_issue.line,
                    )
                if rate_limit_issue is not None:
                    if wait_for_quota_reset(
                        codex_bin=codex_bin,
                        repo_root=repo_root,
                        model=args.model,
                        logger=logger,
                        interval=args.quota_wait_interval,
                        max_attempts=args.max_quota_waits,
                        step_name="implement",
                        matched_line=rate_limit_issue.line,
                        explicit_never_approval=explicit_never_approval,
                    ):
                        continue
                    return graceful_quota_exit(
                        logger=logger,
                        step_name="implement",
                        matched_line=rate_limit_issue.line,
                    )
                logger.log(
                    "ERROR: implement step failed with code "
                    f"{implement_result.returncode}.",
                )
                return implement_result.returncode

            if is_plan_done(implement_result.last_message):
                logger.log("Codex returned PLAN IS DONE.")
                if args.docs_review_interval > 0:
                    docs_review_exit = run_docs_review(
                        codex_bin=codex_bin,
                        repo_root=repo_root,
                        model=args.model,
                        cycle_number=cycle_number,
                        logs_root=logs_root,
                        logger=logger,
                        codex_exec_cooldown_seconds=args.codex_exec_cooldown_seconds,
                        retryable_rate_limit_max_retries=(
                            args.retryable_rate_limit_max_retries
                        ),
                        retryable_rate_limit_backoff_base_seconds=(
                            args.retryable_rate_limit_backoff_base_seconds
                        ),
                        retryable_rate_limit_backoff_max_seconds=(
                            args.retryable_rate_limit_backoff_max_seconds
                        ),
                        quota_wait_interval=args.quota_wait_interval,
                        max_quota_waits=args.max_quota_waits,
                        explicit_never_approval=explicit_never_approval,
                    )
                    if docs_review_exit is not None:
                        return docs_review_exit
                precommit_policy_exit = enforce_precommit_policy(
                    uv_bin=uv_bin,
                    codex_bin=codex_bin,
                    repo_root=repo_root,
                    model=args.model,
                    cycle_number=cycle_number,
                    logs_root=logs_root,
                    logger=logger,
                    codex_exec_cooldown_seconds=args.codex_exec_cooldown_seconds,
                    retryable_rate_limit_max_retries=(
                        args.retryable_rate_limit_max_retries
                    ),
                    retryable_rate_limit_backoff_base_seconds=(
                        args.retryable_rate_limit_backoff_base_seconds
                    ),
                    retryable_rate_limit_backoff_max_seconds=(
                        args.retryable_rate_limit_backoff_max_seconds
                    ),
                    quota_wait_interval=args.quota_wait_interval,
                    max_quota_waits=args.max_quota_waits,
                    explicit_never_approval=explicit_never_approval,
                    precommit_repair_max_attempts=args.precommit_repair_max_attempts,
                )
                if precommit_policy_exit is not None:
                    return precommit_policy_exit
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
                rate_limit_issue = detect_rate_limit_issue(
                    implement_result.output,
                    implement_result.last_message,
                )
                if (
                    rate_limit_issue is not None
                    and rate_limit_issue.retryable
                    and wait_for_retryable_rate_limit(
                        logger=logger,
                        step_name="implement",
                        matched_line=rate_limit_issue.line,
                        attempt=1,
                        max_attempts=1,
                        backoff_base_seconds=(
                            args.retryable_rate_limit_backoff_base_seconds
                        ),
                        backoff_max_seconds=args.retryable_rate_limit_backoff_max_seconds,
                    )
                ):
                    continue
                if rate_limit_issue is not None:
                    if wait_for_quota_reset(
                        codex_bin=codex_bin,
                        repo_root=repo_root,
                        model=args.model,
                        logger=logger,
                        interval=args.quota_wait_interval,
                        max_attempts=args.max_quota_waits,
                        step_name="implement",
                        matched_line=rate_limit_issue.line,
                        explicit_never_approval=explicit_never_approval,
                    ):
                        continue
                    return graceful_quota_exit(
                        logger=logger,
                        step_name="implement",
                        matched_line=rate_limit_issue.line,
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

            # --- Traceability Auto-Sync ---
            if pending_item is not None:
                if sync_traceability_sha(
                    plan_path=plan_path,
                    item_id=pending_item.identifier,
                    actual_sha=head_after_implement,
                    logger=logger,
                ):
                    logger.log(
                        f"Traceability auto-synced for {pending_item.identifier} "
                        f"to {head_after_implement[:7]}.",
                    )
                else:
                    logger.log(
                        f"Traceability for {pending_item.identifier} was already "
                        "correct or could not be synced.",
                    )
            # ------------------------------

            # --- Runner-Led Verification ---
            verification_report = None
            if pending_item is not None:
                verification_report = run_verification_commands(
                    commands=pending_item.verification_commands,
                    cwd=repo_root,
                    logger=logger,
                )
            # ------------------------------

            review_prompt = build_review_prompt(
                repo_root=repo_root,
                plan_path=plan_path,
                implemented_item=pending_item,
                implemented_commit=head_after_implement,
                branch=branch,
                upstream=upstream,
                verification_report=verification_report,
            )
            logger.log("Review prompt prepared and saved to per-step artifact file.")
            review_retryable_attempt = 0
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
                    codex_exec_cooldown_seconds=args.codex_exec_cooldown_seconds,
                    explicit_never_approval=explicit_never_approval,
                )
                if not enforce_no_verify_policy_or_fail(
                    logger=logger,
                    step_name="review_fix_push",
                    output=review_result.output,
                    last_message=review_result.last_message,
                ):
                    return 1
                if review_result.returncode == 0:
                    break
                rate_limit_issue = detect_rate_limit_issue(
                    review_result.output,
                    review_result.last_message,
                )
                if rate_limit_issue is not None and rate_limit_issue.retryable:
                    review_retryable_attempt += 1
                    if wait_for_retryable_rate_limit(
                        logger=logger,
                        step_name="review_fix_push",
                        matched_line=rate_limit_issue.line,
                        attempt=review_retryable_attempt,
                        max_attempts=args.retryable_rate_limit_max_retries,
                        backoff_base_seconds=(
                            args.retryable_rate_limit_backoff_base_seconds
                        ),
                        backoff_max_seconds=args.retryable_rate_limit_backoff_max_seconds,
                    ):
                        continue
                    return graceful_quota_exit(
                        logger=logger,
                        step_name="review_fix_push",
                        matched_line=rate_limit_issue.line,
                    )
                if rate_limit_issue is not None:
                    if wait_for_quota_reset(
                        codex_bin=codex_bin,
                        repo_root=repo_root,
                        model=args.model,
                        logger=logger,
                        interval=args.quota_wait_interval,
                        max_attempts=args.max_quota_waits,
                        step_name="review_fix_push",
                        matched_line=rate_limit_issue.line,
                        explicit_never_approval=explicit_never_approval,
                    ):
                        continue
                    return graceful_quota_exit(
                        logger=logger,
                        step_name="review_fix_push",
                        matched_line=rate_limit_issue.line,
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
                docs_review_exit = run_docs_review(
                    codex_bin=codex_bin,
                    repo_root=repo_root,
                    model=args.model,
                    cycle_number=cycle_number,
                    logs_root=logs_root,
                    logger=logger,
                    codex_exec_cooldown_seconds=args.codex_exec_cooldown_seconds,
                    retryable_rate_limit_max_retries=(
                        args.retryable_rate_limit_max_retries
                    ),
                    retryable_rate_limit_backoff_base_seconds=(
                        args.retryable_rate_limit_backoff_base_seconds
                    ),
                    retryable_rate_limit_backoff_max_seconds=(
                        args.retryable_rate_limit_backoff_max_seconds
                    ),
                    quota_wait_interval=args.quota_wait_interval,
                    max_quota_waits=args.max_quota_waits,
                    explicit_never_approval=explicit_never_approval,
                )
                if docs_review_exit is not None:
                    return docs_review_exit

            precommit_policy_exit = enforce_precommit_policy(
                uv_bin=uv_bin,
                codex_bin=codex_bin,
                repo_root=repo_root,
                model=args.model,
                cycle_number=cycle_number,
                logs_root=logs_root,
                logger=logger,
                codex_exec_cooldown_seconds=args.codex_exec_cooldown_seconds,
                retryable_rate_limit_max_retries=(
                    args.retryable_rate_limit_max_retries
                ),
                retryable_rate_limit_backoff_base_seconds=(
                    args.retryable_rate_limit_backoff_base_seconds
                ),
                retryable_rate_limit_backoff_max_seconds=(
                    args.retryable_rate_limit_backoff_max_seconds
                ),
                quota_wait_interval=args.quota_wait_interval,
                max_quota_waits=args.max_quota_waits,
                explicit_never_approval=explicit_never_approval,
                precommit_repair_max_attempts=args.precommit_repair_max_attempts,
            )
            if precommit_policy_exit is not None:
                return precommit_policy_exit

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
