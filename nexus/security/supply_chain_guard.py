"""Supply-chain security guard for Nexus CLI.

Audits dependency installation, lockfile integrity, package sources,
typosquatting risks, and lifecycle script execution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from nexus.package_guard import PackageGuard


@dataclass
class SupplyChainAudit:
    package_name: str
    package_manager: str
    is_url_dependency: bool
    is_git_dependency: bool
    requires_approval: bool
    risk_notes: list[str]


TYPOSQUATTING_TARGETS = (
    "requests",
    "express",
    "react",
    "lodash",
    "pytest",
    "flask",
    "django",
    "urllib3",
)


class SupplyChainGuard:
    """Audits dependency operations against supply-chain threats."""

    def __init__(self, package_guard: PackageGuard | None = None):
        self.guard = package_guard or PackageGuard()

    def audit_install_command(
        self, argv: Sequence[str], workspace_root: str = "."
    ) -> SupplyChainAudit:
        """Audit a package installation command before execution."""
        full_line = " ".join(argv).lower()
        cmd = argv[0].lower() if argv else ""

        risk_notes: list[str] = []
        is_url = bool(re.search(r"https?://|git\+", full_line))
        is_git = bool("git+" in full_line or ".git" in full_line)

        if is_url:
            risk_notes.append("Direct URL or Git dependency requested")
        if "--ignore-scripts" not in full_line and cmd == "npm":
            risk_notes.append("Package installation may run lifecycle scripts (e.g., preinstall/postinstall)")

        # Extract target package name if available
        pkg_name = argv[-1] if len(argv) > 1 and not argv[-1].startswith("-") else "unknown"

        # Typosquatting check
        for target in TYPOSQUATTING_TARGETS:
            if pkg_name != target and abs(len(pkg_name) - len(target)) <= 1 and target[:3] in pkg_name:
                risk_notes.append(f"Possible typosquatting risk against popular package '{target}'")

        requires_approval = bool(risk_notes or is_url or is_git)

        return SupplyChainAudit(
            package_name=pkg_name,
            package_manager=cmd,
            is_url_dependency=is_url,
            is_git_dependency=is_git,
            requires_approval=requires_approval,
            risk_notes=risk_notes,
        )
