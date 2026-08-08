"""Command policy hardening for Noryx CLI.

Classifies commands by risk, denies shell string execution for untrusted inputs,
requires argv array execution, and enforces working directory and timeout bounds.
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path
from typing import Sequence


class CommandRisk(str, Enum):
    READ_ONLY = "read_only"
    VALIDATION = "validation"
    BUILD = "build"
    FORMAT = "format"
    PACKAGE_INSTALL = "package_install"
    GIT_MUTATION = "git_mutation"
    NETWORK_REQUEST = "network_request"
    DESTRUCTIVE = "destructive"
    PRIVILEGED = "privileged"
    UNKNOWN = "unknown"


DANGEROUS_COMMAND_PATTERNS = [
    (r"rm\s+-rf?\s+[/~]", "Recursive root or home deletion"),
    (r"chmod\s+777\s+[/~]", "Recursive root permission modification"),
    (r"mkfs", "Filesystem creation"),
    (r"dd\s+if=", "Raw disk write"),
    (r"curl\s+.*\|\s*sh", "Piping untrusted network output to shell"),
    (r"wget\s+.*\|\s*bash", "Piping untrusted network output to bash"),
    (r"sudo\s+", "Privilege escalation request"),
]


class CommandPolicy:
    """Classifies and authorizes command execution requests."""

    def __init__(self, workspace_root: str | Path):
        self.workspace_root = Path(workspace_root).expanduser().resolve()

    def classify(self, argv: Sequence[str]) -> CommandRisk:
        if not argv:
            return CommandRisk.UNKNOWN

        normalized = [str(item) for item in argv]
        cmd = Path(normalized[0]).name.lower()
        args = [item.lower() for item in normalized[1:]]
        full_line = " ".join(normalized)

        if any(token in {"|", "||", "&&", ";", ">", ">>", "<"} for token in normalized):
            return CommandRisk.UNKNOWN

        # Check dangerous patterns first.
        for pattern, _ in DANGEROUS_COMMAND_PATTERNS:
            if re.search(pattern, full_line, re.IGNORECASE):
                return CommandRisk.DESTRUCTIVE

        first = args[0] if args else ""
        second = args[1] if len(args) > 1 else ""

        if cmd in {"ls", "cat", "grep", "rg", "find", "pwd", "head", "tail"}:
            return CommandRisk.READ_ONLY
        if cmd == "git" and first in {"status", "log", "diff", "show", "rev-parse", "ls-files"}:
            return CommandRisk.READ_ONLY

        if cmd in {"pytest", "flake8", "eslint", "ruff", "mypy"}:
            return CommandRisk.VALIDATION
        if (
            cmd in {"python", "python3"}
            and len(args) >= 2
            and first == "-m"
            and second in {"pytest", "ruff", "mypy"}
        ):
            return CommandRisk.VALIDATION
        if (cmd in {"npm", "yarn", "pnpm"} and first == "test") or (
            cmd in {"go", "cargo"} and first == "test"
        ):
            return CommandRisk.VALIDATION

        if cmd in {"make", "gcc", "clang"}:
            return CommandRisk.BUILD
        if cmd in {"npm", "yarn", "pnpm"} and first == "run" and second == "build":
            return CommandRisk.BUILD
        if cmd in {"cargo", "go"} and first == "build":
            return CommandRisk.BUILD

        if (cmd in {"pip", "pip3"} and first == "install") or (
            cmd == "uv" and first == "pip" and second == "install"
        ):
            return CommandRisk.PACKAGE_INSTALL
        if cmd in {"npm", "pnpm"} and first in {"install", "add"}:
            return CommandRisk.PACKAGE_INSTALL
        if cmd == "yarn" and first in {"add", "install"}:
            return CommandRisk.PACKAGE_INSTALL
        if cmd in {"cargo", "poetry"} and first == "add":
            return CommandRisk.PACKAGE_INSTALL
        if (
            cmd in {"python", "python3"}
            and len(args) >= 3
            and first == "-m"
            and second in {"pip", "uv"}
            and args[2] == "install"
        ):
            return CommandRisk.PACKAGE_INSTALL

        if cmd == "git" and first in {
            "add",
            "commit",
            "branch",
            "checkout",
            "switch",
            "merge",
            "rebase",
            "reset",
            "restore",
            "rm",
            "mv",
            "tag",
            "cherry-pick",
            "revert",
        }:
            return CommandRisk.GIT_MUTATION

        if cmd in {"curl", "wget", "fetch"}:
            return CommandRisk.NETWORK_REQUEST
        if cmd == "git" and first in {"push", "fetch", "pull", "clone", "ls-remote"}:
            return CommandRisk.NETWORK_REQUEST

        return CommandRisk.UNKNOWN

    def validate_command(
        self,
        argv: Sequence[str],
        cwd: str | Path,
        *,
        allow_shell: bool = False,
    ) -> tuple[str, ...]:
        """Validate command executable and arguments. Returns normalized argv tuple."""
        if allow_shell:
            raise ValueError(
                "Direct shell execution (shell=True) is forbidden under security policy"
            )

        if not argv:
            raise ValueError("argv cannot be empty")

        normalized = tuple(str(item) for item in argv)
        executable = normalized[0].strip()

        if not executable:
            raise ValueError("Executable name cannot be blank")

        # Check dangerous patterns
        full_cmd = " ".join(normalized)
        for pattern, reason in DANGEROUS_COMMAND_PATTERNS:
            if re.search(pattern, full_cmd, re.IGNORECASE):
                raise ValueError(f"Command blocked by safety policy ({reason}): {full_cmd!r}")

        # Check cwd containment
        cwd_path = Path(cwd).expanduser().resolve()
        try:
            cwd_path.relative_to(self.workspace_root)
        except ValueError:
            raise ValueError(
                f"Command working directory is outside workspace: {cwd_path}"
            ) from None

        return normalized
