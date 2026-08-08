"""
Safety Layer — permission system for dangerous operations.

Classifies operations as SAFE, WARN, DANGEROUS, or BLOCKED, and enforces
appropriate guardrails before execution.

Architecture:
    Operation → Classification → Permission Check → Execute or Block
"""

import re
from dataclasses import dataclass
from enum import Enum


class SafetyLevel(str, Enum):
    """Classification of operation safety."""

    SAFE = "safe"  # No confirmation needed
    WARN = "warn"  # Log a warning but proceed
    DANGEROUS = "dangerous"  # Requires user confirmation
    BLOCKED = "blocked"  # Never allowed


@dataclass
class SafetyCheck:
    """Result of a safety evaluation."""

    level: SafetyLevel
    operation: str
    reason: str
    details: str = ""
    requires_confirmation: bool = False
    confirmed: bool = False

    @property
    def is_allowed(self) -> bool:
        if self.level == SafetyLevel.BLOCKED:
            return False
        if self.level == SafetyLevel.DANGEROUS and not self.confirmed:
            return False
        return True

    def format_warning(self) -> str:
        """Format a human-readable warning."""
        icons = {
            SafetyLevel.SAFE: "✅",
            SafetyLevel.WARN: "⚠️",
            SafetyLevel.DANGEROUS: "🛑",
            SafetyLevel.BLOCKED: "🚫",
        }
        icon = icons.get(self.level, "❓")
        msg = f"{icon} [{self.level.value.upper()}] {self.reason}"
        if self.details:
            msg += f"\n   {self.details}"
        return msg


# ── Dangerous Command Patterns ───────────────────────────────────────────────

_COMMAND_PATTERNS: list[tuple[str, SafetyLevel, str]] = [
    # BLOCKED — never allow these
    (r"\brm\s+-rf\s+/\s*$", SafetyLevel.BLOCKED, "Recursive deletion of root filesystem"),
    (r"\brm\s+-rf\s+/\w+\s*$", SafetyLevel.BLOCKED, "Recursive deletion of top-level directory"),
    (r"\b(mkfs|fdisk|dd\s+if=)\b", SafetyLevel.BLOCKED, "Disk formatting/overwriting"),
    (r"\b:\(\)\{.*\}\s*;", SafetyLevel.BLOCKED, "Fork bomb detected"),
    (r"\b(chmod|chown)\s+.*-R\s+/\s*$", SafetyLevel.BLOCKED, "Recursive permission change on root"),
    (
        r"\bcurl\b.*\|\s*(bash|sh|zsh)",
        SafetyLevel.BLOCKED,
        "Piping remote script directly to shell",
    ),
    (
        r"\bwget\b.*\|\s*(bash|sh|zsh)",
        SafetyLevel.BLOCKED,
        "Piping remote script directly to shell",
    ),
    # DANGEROUS — require confirmation
    (r"\brm\s+-rf\b", SafetyLevel.DANGEROUS, "Recursive file deletion"),
    (r"\brm\s+-r\b", SafetyLevel.DANGEROUS, "Recursive file deletion"),
    (r"\brm\s+.*\*", SafetyLevel.DANGEROUS, "Wildcard file deletion"),
    (
        r"\bgit\s+push\s+.*--force\b",
        SafetyLevel.DANGEROUS,
        "Force push (may overwrite remote history)",
    ),
    (r"\bgit\s+push\s+.*-f\b", SafetyLevel.DANGEROUS, "Force push (may overwrite remote history)"),
    (
        r"\bgit\s+reset\s+--hard\b",
        SafetyLevel.DANGEROUS,
        "Hard reset (discards uncommitted changes)",
    ),
    (r"\bgit\s+clean\s+-fd\b", SafetyLevel.DANGEROUS, "Git clean (removes untracked files)"),
    (r"\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", SafetyLevel.DANGEROUS, "Database DROP operation"),
    (r"\bTRUNCATE\s+TABLE\b", SafetyLevel.DANGEROUS, "Database TRUNCATE operation"),
    (r"\bDELETE\s+FROM\b(?!.*WHERE)", SafetyLevel.DANGEROUS, "DELETE without WHERE clause"),
    (
        r"\bdocker\s+(rm|rmi|system\s+prune)\b",
        SafetyLevel.DANGEROUS,
        "Docker container/image removal",
    ),
    (
        r"\bdocker\s+compose\s+down\s+-v\b",
        SafetyLevel.DANGEROUS,
        "Docker compose down with volumes",
    ),
    (r"\bkubectl\s+delete\b", SafetyLevel.DANGEROUS, "Kubernetes resource deletion"),
    (r"\bsudo\b", SafetyLevel.DANGEROUS, "Running with superuser privileges"),
    (r"\bnpm\s+publish\b", SafetyLevel.DANGEROUS, "Publishing to npm registry"),
    (
        r"\bpip\s+install\s+--user\b.*-e\s+git",
        SafetyLevel.DANGEROUS,
        "Installing from remote git repo",
    ),
    (r"\bcurl\b.*-o\s*/", SafetyLevel.DANGEROUS, "Downloading file to system directory"),
    # WARN — log warning but proceed
    (r"\bgit\s+push\b", SafetyLevel.WARN, "Pushing to remote repository"),
    (r"\bgit\s+merge\b", SafetyLevel.WARN, "Merging branches"),
    (r"\bgit\s+rebase\b", SafetyLevel.WARN, "Rebasing (rewrites history)"),
    (r"\bnpm\s+install\b", SafetyLevel.WARN, "Installing npm packages"),
    (r"\bpip\s+install\b", SafetyLevel.WARN, "Installing Python packages"),
    (r"\bcargo\s+install\b", SafetyLevel.WARN, "Installing Rust packages"),
    (r"\brm\s+\S+", SafetyLevel.WARN, "Deleting a file"),
    (r"\bmv\s+\S+", SafetyLevel.WARN, "Moving/renaming a file"),
    (r"\bchmod\b", SafetyLevel.WARN, "Changing file permissions"),
    (r"\bchown\b", SafetyLevel.WARN, "Changing file ownership"),
]

# ── File Operation Patterns ──────────────────────────────────────────────────

_FILE_WRITE_PATTERNS: list[tuple[str, SafetyLevel, str]] = [
    # DANGEROUS — writing to system files
    (
        r"^/(etc|usr|bin|sbin|lib|var|boot|proc|sys|dev)/",
        SafetyLevel.BLOCKED,
        "Writing to system directory",
    ),
    (r"^/root/", SafetyLevel.DANGEROUS, "Writing to root's home directory"),
    (r"^~/.ssh/", SafetyLevel.DANGEROUS, "Writing to SSH directory"),
    (r"^~/.gnupg/", SafetyLevel.DANGEROUS, "Writing to GPG directory"),
    # WARN — overwriting important files
    (r"\.env$", SafetyLevel.WARN, "Writing to .env file (may contain secrets)"),
    (r"\.gitignore$", SafetyLevel.WARN, "Modifying .gitignore"),
    (
        r"package-lock\.json$",
        SafetyLevel.WARN,
        "Modifying package-lock.json (usually auto-generated)",
    ),
    (r"yarn\.lock$", SafetyLevel.WARN, "Modifying yarn.lock (usually auto-generated)"),
    (r"Cargo\.lock$", SafetyLevel.WARN, "Modifying Cargo.lock (usually auto-generated)"),
]

# ── Content Patterns (secrets/keys) ──────────────────────────────────────────

_SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"(?:api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{20,}", "API key"),
    (
        r"(?:secret|token|password|passwd|pwd)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{8,}",
        "Secret/token/password",
    ),
    (r"(?:aws_access_key_id|aws_secret_access_key)\s*[:=]", "AWS credentials"),
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI API key"),
    (r"ghp_[A-Za-z0-9]{36}", "GitHub personal access token"),
    (r"glpat-[A-Za-z0-9\-]{20}", "GitLab personal access token"),
    (r"nvapi-[A-Za-z0-9\-_]{20,}", "NVIDIA API key"),
    (r"-----BEGIN\s+(RSA|EC|DSA|OPENSSH)\s+PRIVATE\s+KEY-----", "Private key"),
    (r"xoxb-[A-Za-z0-9\-]+", "Slack bot token"),
    (r"xoxp-[A-Za-z0-9\-]+", "Slack user token"),
]


class SafetyLayer:
    """
    Permission system for dangerous operations.

    Evaluates commands and file operations before execution, classifying them
    by safety level and requiring confirmation for dangerous operations.

    Usage:
        safety = SafetyLayer()

        # Check a command
        check = safety.check_command("rm -rf /tmp/mydir")
        if check.requires_confirmation:
            # Ask user
            check.confirmed = True
        if check.is_allowed:
            # Execute

        # Check file write
        check = safety.check_file_write("/etc/passwd", content)
        if not check.is_allowed:
            # Block

        # Check content for secrets
        warnings = safety.check_content_for_secrets(content)
    """

    def __init__(self):
        self._project_rules: dict[str, list[str]] = {}  # From NEXUS.md
        self._blocked_commands: list[str] = []  # User-defined blocked commands
        self._allowed_commands: list[str] = []  # User-defined always-allowed commands

    def configure_from_rules(self, rules: dict):
        """
        Configure safety rules from NEXUS.md project rules.

        Supported rule keys:
        - blocked_commands: list of regex patterns to always block
        - allowed_commands: list of regex patterns to always allow
        - require_confirmation: list of regex patterns requiring confirmation
        """
        self._blocked_commands = rules.get("blocked_commands", [])
        self._allowed_commands = rules.get("allowed_commands", [])

    def check_command(self, command: str) -> SafetyCheck:
        """
        Evaluate the safety of a shell command.

        Returns a SafetyCheck with the appropriate classification.
        """
        # Check user-defined blocked commands first
        for pattern in self._blocked_commands:
            if re.search(pattern, command, re.IGNORECASE):
                return SafetyCheck(
                    level=SafetyLevel.BLOCKED,
                    operation=command,
                    reason=f"Blocked by project rules: matches '{pattern}'",
                )

        # Check against built-in patterns
        for pattern, level, reason in _COMMAND_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                check = SafetyCheck(
                    level=level,
                    operation=command,
                    reason=reason,
                    details=f"Command: {command}",
                    requires_confirmation=(level == SafetyLevel.DANGEROUS),
                )

                return check

        # Project allow rules may suppress ordinary prompts, but can never
        # downgrade a built-in dangerous or blocked classification.
        for pattern in self._allowed_commands:
            if re.search(pattern, command, re.IGNORECASE):
                return SafetyCheck(
                    level=SafetyLevel.SAFE,
                    operation=command,
                    reason="Allowed by project rules",
                )

        return SafetyCheck(
            level=SafetyLevel.SAFE,
            operation=command,
            reason="No safety concerns detected",
        )

    def check_file_write(self, filepath: str, content: str = "") -> SafetyCheck:
        """Evaluate the safety of writing to a file."""
        for pattern, level, reason in _FILE_WRITE_PATTERNS:
            if re.search(pattern, filepath, re.IGNORECASE):
                check = SafetyCheck(
                    level=level,
                    operation=f"write to {filepath}",
                    reason=reason,
                    details=filepath,
                    requires_confirmation=(level == SafetyLevel.DANGEROUS),
                )
                return check

        # Check content for secrets if we're writing to a git-tracked file
        if content:
            secret_warnings = self.check_content_for_secrets(content)
            if secret_warnings:
                first_warning = secret_warnings[0]
                return SafetyCheck(
                    level=SafetyLevel.WARN,
                    operation=f"write to {filepath}",
                    reason=f"Content may contain {first_warning}",
                    details=f"Found {len(secret_warnings)} potential secret(s)",
                )

        return SafetyCheck(
            level=SafetyLevel.SAFE,
            operation=f"write to {filepath}",
            reason="No safety concerns",
        )

    def check_content_for_secrets(self, content: str) -> list[str]:
        """Check file content for hardcoded secrets/credentials."""
        warnings = []
        for pattern, description in _SECRET_PATTERNS:
            if re.search(pattern, content, re.IGNORECASE):
                warnings.append(description)
        return warnings

    def confirm_operation(self, operation: str, pattern: str = ""):
        """Deprecated: confirmations are single-use exact pending operations."""
        return None

    def check_git_operation(self, git_args: list[str]) -> SafetyCheck:
        """Evaluate the safety of a git operation."""
        command = "git " + " ".join(git_args)
        return self.check_command(command)

    def get_safety_summary(self) -> str:
        """Get a summary of safety rules in effect."""
        lines = ["🛡️ Safety Layer Active"]
        lines.append(f"  Blocked patterns: {len(_COMMAND_PATTERNS) + len(self._blocked_commands)}")
        lines.append(f"  Secret patterns: {len(_SECRET_PATTERNS)}")
        if self._blocked_commands:
            lines.append(f"  Project-blocked: {len(self._blocked_commands)}")
        if self._allowed_commands:
            lines.append(f"  Project-allowed: {len(self._allowed_commands)}")
        return "\n".join(lines)
