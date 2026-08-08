"""
Built-in Hooks — auto-format, auto-lint, auto-test, security scan.

Security:
  - All commands use argv lists, never shell strings
  - shlex.split is NOT used on untrusted user input
  - Paths are validated against the workspace root
  - Each hook declares a failure_policy
"""

import logging
import os
import re

from nexus.hooks.base import (
    BaseHook,
    HookContext,
    HookEvent,
    HookFailurePolicy,
    HookResult,
    HookType,
    validate_hook_path,
)

logger = logging.getLogger(__name__)


class AutoFormatHook(BaseHook):
    """Auto-format files after editing."""

    name = "auto_format"
    description = "Auto-format files after editing (prettier, black, gofmt)"
    events = [HookEvent.AFTER_FILE_EDIT, HookEvent.AFTER_FILE_CREATE]
    hook_type = HookType.SHELL
    enabled = False  # Disabled by default
    priority = 30
    failure_policy = HookFailurePolicy.WARN

    def get_command(self, context: HookContext) -> list[str]:
        path = context.file_path
        # SECURITY: Validate path against workspace
        if context.workspace_root and not validate_hook_path(path, context.workspace_root):
            logger.warning("AutoFormatHook: path outside workspace: %s", path)
            return []
        if path.endswith(".py"):
            return ["ruff", "format", path]
        elif path.endswith((".js", ".jsx", ".ts", ".tsx", ".css", ".json", ".md")):
            return ["npx", "prettier", "--write", path]
        elif path.endswith(".go"):
            return ["gofmt", "-w", path]
        elif path.endswith(".rs"):
            return ["rustfmt", path]
        return []


class AutoLintHook(BaseHook):
    """Auto-lint files after editing."""

    name = "auto_lint"
    description = "Run linter after file edits"
    events = [HookEvent.AFTER_FILE_EDIT]
    hook_type = HookType.SHELL
    enabled = False  # Disabled by default
    priority = 25
    failure_policy = HookFailurePolicy.WARN

    def get_command(self, context: HookContext) -> list[str]:
        path = context.file_path
        if context.workspace_root and not validate_hook_path(path, context.workspace_root):
            logger.warning("AutoLintHook: path outside workspace: %s", path)
            return []
        if path.endswith(".py"):
            return ["ruff", "check", path, "--no-fix"]
        elif path.endswith((".js", ".jsx", ".ts", ".tsx")):
            return ["npx", "eslint", path, "--no-error-on-unmatched-pattern"]
        elif path.endswith(".rs"):
            return ["cargo", "clippy", "--quiet"]
        return []


class PreCommitTestHook(BaseHook):
    """Run tests before committing."""

    name = "pre_commit_test"
    description = "Run tests before git commit"
    events = [HookEvent.BEFORE_COMMIT]
    hook_type = HookType.SHELL
    enabled = False
    priority = 80
    failure_policy = HookFailurePolicy.BLOCK

    def get_command(self, context: HookContext) -> list[str]:
        # SECURITY FIX: Do NOT use shlex.split on user-provided metadata.
        # Only accept structured test commands from validated project config.
        test_cmd = context.metadata.get("test_command")
        if test_cmd and isinstance(test_cmd, list):
            # Accept only pre-validated argv lists from project config
            return test_cmd
        elif test_cmd and isinstance(test_cmd, str):
            # SECURITY: Reject string commands from metadata.
            # String commands from untrusted sources could contain shell injection.
            logger.warning(
                "PreCommitTestHook: Rejected string test_command from metadata. "
                "Only argv lists are accepted for security."
            )
            return ["python", "-m", "pytest", "-x", "-q"]
        # Default: run Python tests (most common for this project)
        return ["python", "-m", "pytest", "-x", "-q"]


class SecurityScanHook(BaseHook):
    """Scan for secrets before pushing."""

    name = "security_scan"
    description = "Scan for hardcoded secrets before git push"
    events = [HookEvent.BEFORE_PUSH]
    hook_type = HookType.BLOCK
    enabled = False
    priority = 90
    failure_policy = HookFailurePolicy.BLOCK

    def execute(self, context: HookContext) -> HookResult:
        """Perform a naive secret scan without executing external tools."""
        working_dir = context.metadata.get("working_dir", ".")

        # Validate working_dir
        if context.workspace_root and not validate_hook_path(working_dir, context.workspace_root):
            return HookResult(
                hook_name=self.name,
                event=context.event,
                success=False,
                output=f"Working directory outside workspace: {working_dir}",
                failure_policy=self.failure_policy,
            )

        # Naive secret scan (no external tools required)
        secret_pattern = re.compile(r"password|secret|api_key|private_key", re.IGNORECASE)
        found = False
        for root, dirs, files in os.walk(working_dir):
            dirs[:] = [
                d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv", "venv"}
            ]
            for file in files:
                if file.endswith((".py", ".js", ".ts", ".env")):
                    filepath = os.path.join(root, file)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            for line in f:
                                if secret_pattern.search(line):
                                    found = True
                                    break
                    except (OSError, UnicodeDecodeError):
                        continue
        return HookResult(
            hook_name=self.name,
            event=context.event,
            success=not found,
            output="Secrets found!" if found else "No secrets found.",
            blocked=found,
            failure_policy=self.failure_policy,
        )


class NotifyOnErrorHook(BaseHook):
    """Log errors for debugging."""

    name = "notify_on_error"
    description = "Log errors for debugging"
    events = [HookEvent.ON_ERROR]
    hook_type = HookType.NOTIFY
    enabled = True
    priority = 50
    failure_policy = HookFailurePolicy.WARN

    def execute(self, context: HookContext) -> HookResult:
        return HookResult(
            hook_name=self.name,
            event=context.event,
            success=True,
            output=f"Error occurred: {context.error_message}",
            failure_policy=self.failure_policy,
        )


class SessionStartHook(BaseHook):
    """Actions to perform when a session starts."""

    name = "session_start"
    description = "Setup actions on session start"
    events = [HookEvent.ON_SESSION_START]
    hook_type = HookType.NOTIFY
    enabled = True
    priority = 10
    failure_policy = HookFailurePolicy.WARN

    def execute(self, context: HookContext) -> HookResult:
        return HookResult(
            hook_name=self.name,
            event=context.event,
            success=True,
            output="Session started",
            failure_policy=self.failure_policy,
        )


# ── Built-in hook registry ──────────────────────────────────────────────────

ALL_BUILTIN_HOOKS: list[type[BaseHook]] = [
    AutoFormatHook,
    AutoLintHook,
    PreCommitTestHook,
    SecurityScanHook,
    NotifyOnErrorHook,
    SessionStartHook,
]


def create_builtin_hooks() -> list[BaseHook]:
    """Create instances of all built-in hooks."""
    return [cls() for cls in ALL_BUILTIN_HOOKS]
