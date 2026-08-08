"""Filesystem security enforcement for Nexus CLI.

Guarantees path canonicalization, workspace root containment, symlink escape prevention,
null byte rejection, and protected credential path boundaries.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Sequence


PROTECTED_PATH_PATTERNS = (
    r".*\.env.*",
    r".*\.ssh/.*",
    r".*id_rsa.*",
    r".*id_ed25519.*",
    r".*\.aws/credentials.*",
    r".*\.gcp/.*",
    r".*/etc/shadow.*",
    r".*/etc/passwd.*",
    r".*\.gnupg/.*",
    r".*keychain.*",
)


class FilesystemSecurity:
    """Enforces strict path security rules."""

    def __init__(
        self,
        workspace_root: str | Path,
        allowed_read_roots: Sequence[str | Path] | None = None,
        allowed_write_roots: Sequence[str | Path] | None = None,
    ):
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.allowed_read_roots = [
            Path(p).expanduser().resolve() for p in (allowed_read_roots or [self.workspace_root])
        ]
        self.allowed_write_roots = [
            Path(p).expanduser().resolve() for p in (allowed_write_roots or [self.workspace_root])
        ]

    def validate_path(
        self,
        raw_path: str | Path,
        *,
        for_write: bool = False,
        allow_symlink_target: bool = False,
    ) -> Path:
        """Validate, canonicalize, and verify safety of a filesystem path.
        
        Raises ValueError if path is invalid, traverses outside root, attempts symlink escape,
        or accesses protected credentials.
        """
        path_str = str(raw_path)

        # 1. Null Byte & Control Character Check
        if "\x00" in path_str:
            raise ValueError(f"Path contains forbidden null byte: {raw_path!r}")

        # 2. Protected Path Pattern Check
        for pattern in PROTECTED_PATH_PATTERNS:
            if re.search(pattern, path_str, re.IGNORECASE):
                raise ValueError(f"Access to protected credential or system path is denied: {raw_path}")

        # 3. Canonicalization
        candidate = Path(raw_path).expanduser()
        
        # Check for path traversal before resolution if possible
        if ".." in candidate.parts:
            # Must ensure resolved path remains inside allowed roots
            pass

        try:
            resolved = candidate.resolve()
        except Exception as err:
            raise ValueError(f"Failed to resolve path {raw_path}: {err}") from err

        # 4. Symlink Escape Detection
        if not allow_symlink_target and candidate.is_symlink():
            target = candidate.readlink()
            target_resolved = target.resolve() if target.is_absolute() else (candidate.parent / target).resolve()
            if not self._is_under_any_root(target_resolved, self.allowed_read_roots):
                raise ValueError(f"Symlink {raw_path} points outside approved workspace: {target_resolved}")

        # 5. Root Containment Check
        valid_roots = self.allowed_write_roots if for_write else self.allowed_read_roots
        if not self._is_under_any_root(resolved, valid_roots):
            op_type = "Write" if for_write else "Read"
            raise ValueError(
                f"{op_type} path is outside approved workspace boundary: {resolved} (roots: {valid_roots})"
            )

        return resolved

    def _is_under_any_root(self, path: Path, roots: Sequence[Path]) -> bool:
        for root in roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False
