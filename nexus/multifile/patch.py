"""
Multi-File Patch Manager — Sprint 8.

Validates and applies coordinated multi-file patches atomically.

Invariants:
- Every file in a patch must exist in the EngineeringChangeSet.file_changes.
- Unknown files are rejected outright.
- Protected paths require explicit acknowledgement.
- Stale file hashes are rejected (snapshot protection).
- Patches are applied in dependency order.
- Partial application cannot produce a success status.
- Per-file and per-hunk results are recorded.
- Atomic rollback on any failure within a patch application.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from nexus.multifile.contracts import ChangeType, EngineeringChangeSet, PlannedFileChange
from nexus.multifile.graph import build_graph, DependencyCycleError

logger = logging.getLogger(__name__)


class PatchApplicationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CONFLICT = "CONFLICT"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class HunkResult:
    hunk_index: int
    path: str
    status: PatchApplicationStatus
    detail: str = ""


@dataclass
class FileApplicationResult:
    path: str
    status: PatchApplicationStatus
    hunks: list[HunkResult] = field(default_factory=list)
    detail: str = ""
    hash_before: str = ""
    hash_after: str = ""


@dataclass
class PatchApplicationResult:
    status: PatchApplicationStatus
    file_results: list[FileApplicationResult] = field(default_factory=list)
    rejected_files: list[str] = field(default_factory=list)
    rolled_back: bool = False
    failure_reason: str = ""

    def is_success(self) -> bool:
        return self.status == PatchApplicationStatus.SUCCESS and not self.rejected_files


class MultiFilePatchManager:
    """Validates and applies coordinated multi-file patches for an EngineeringChangeSet."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_patch(
        self,
        patch_files: dict[str, str],   # path → new content
        cs: EngineeringChangeSet,
    ) -> PatchApplicationResult:
        """Validate a patch (dict of path → content) against the change set.

        Does NOT apply the patch. Returns a PatchApplicationResult describing
        what would happen.
        """
        result = PatchApplicationResult(status=PatchApplicationStatus.SUCCESS)
        change_set_paths = {fc.path for fc in cs.file_changes}

        for path, content in patch_files.items():
            # 1. Unknown file rejection
            if path not in change_set_paths:
                result.rejected_files.append(path)
                result.file_results.append(
                    FileApplicationResult(
                        path=path,
                        status=PatchApplicationStatus.REJECTED,
                        detail=f"'{path}' is not in the change set — unknown file rejected.",
                    )
                )
                continue

            fc = cs.get_file_change(path)

            # 2. Protected path check
            if fc and fc.protected:
                result.rejected_files.append(path)
                result.file_results.append(
                    FileApplicationResult(
                        path=path,
                        status=PatchApplicationStatus.REJECTED,
                        detail=f"'{path}' is protected — requires explicit approval.",
                    )
                )
                continue

            # 3. Generated file direct-edit check
            if fc and fc.generated and fc.change_type not in (
                ChangeType.GENERATED_UPDATE,
            ):
                result.rejected_files.append(path)
                result.file_results.append(
                    FileApplicationResult(
                        path=path,
                        status=PatchApplicationStatus.REJECTED,
                        detail=f"'{path}' is generated — must regenerate via generator, not direct edit.",
                    )
                )
                continue

            # 4. Stale hash check
            actual_hash = _hash_file(self.repo_root / path)
            if fc and fc.file_hash_before and fc.file_hash_before != actual_hash:
                result.rejected_files.append(path)
                result.file_results.append(
                    FileApplicationResult(
                        path=path,
                        status=PatchApplicationStatus.CONFLICT,
                        detail=(
                            f"'{path}' hash mismatch: expected {fc.file_hash_before[:8]}, "
                            f"got {actual_hash[:8]}. Repository changed since plan was created."
                        ),
                        hash_before=actual_hash,
                    )
                )
                continue

            result.file_results.append(
                FileApplicationResult(
                    path=path,
                    status=PatchApplicationStatus.SUCCESS,
                    detail="Validation passed.",
                    hash_before=actual_hash,
                )
            )

        if result.rejected_files:
            result.status = PatchApplicationStatus.REJECTED
            result.failure_reason = (
                f"Patch validation failed: {len(result.rejected_files)} file(s) rejected."
            )

        return result

    def apply_patch(
        self,
        patch_files: dict[str, str],   # path → new content
        cs: EngineeringChangeSet,
        *,
        dry_run: bool = False,
    ) -> PatchApplicationResult:
        """Validate and apply a patch to the repository.

        Applies in dependency order. If any file fails, performs full atomic
        rollback of all files already written.
        """
        # Validate first
        validation = self.validate_patch(patch_files, cs)
        if not validation.is_success():
            return validation

        # Build application order
        try:
            fc_map = {fc.path: fc for fc in cs.file_changes if fc.path in patch_files}
            graph = build_graph(list(fc_map.values()), cs.dependency_edges)
            ordered = graph.topological_sort()
        except DependencyCycleError as exc:
            return PatchApplicationResult(
                status=PatchApplicationStatus.REJECTED,
                failure_reason=str(exc),
            )

        if dry_run:
            return validation

        # Apply with snapshot backup for atomic rollback
        backup: dict[str, str | None] = {}  # path → original content or None (new file)
        applied: list[str] = []
        result = PatchApplicationResult(status=PatchApplicationStatus.SUCCESS)

        for fc in ordered:
            if fc.path not in patch_files:
                continue
            full_path = self.repo_root / fc.path
            new_content = patch_files[fc.path]

            # Backup current state
            if full_path.exists():
                try:
                    backup[fc.path] = full_path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    backup[fc.path] = None
            else:
                backup[fc.path] = None

            # Apply
            hash_before = _hash_file(full_path)
            try:
                if fc.change_type == ChangeType.DELETE:
                    if full_path.exists():
                        full_path.unlink()
                else:
                    full_path.parent.mkdir(parents=True, exist_ok=True)
                    full_path.write_text(new_content, encoding="utf-8")
                applied.append(fc.path)
                hash_after = _hash_file(full_path)
                result.file_results.append(
                    FileApplicationResult(
                        path=fc.path,
                        status=PatchApplicationStatus.SUCCESS,
                        hash_before=hash_before,
                        hash_after=hash_after,
                    )
                )
            except OSError as exc:
                # Rollback everything applied so far
                self._rollback(backup, applied)
                return PatchApplicationResult(
                    status=PatchApplicationStatus.FAILED,
                    rolled_back=True,
                    failure_reason=f"Failed writing '{fc.path}': {exc}",
                    file_results=result.file_results,
                )

        return result

    def apply_unified_diff(
        self,
        diff_text: str,
        cs: EngineeringChangeSet,
        *,
        dry_run: bool = False,
    ) -> PatchApplicationResult:
        """Parse a unified diff and apply it as a coordinated patch.

        Splits the diff into per-file hunks, validates the complete set against
        the change set, then applies in dependency order.
        """
        patch_files = _parse_unified_diff(diff_text)
        if not patch_files:
            return PatchApplicationResult(
                status=PatchApplicationStatus.REJECTED,
                failure_reason="Could not parse any file hunks from unified diff.",
            )
        return self.apply_patch(patch_files, cs, dry_run=dry_run)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _rollback(self, backup: dict[str, str | None], applied: list[str]) -> None:
        """Atomically restore backed-up file states."""
        for path in reversed(applied):
            full_path = self.repo_root / path
            original = backup.get(path)
            try:
                if original is None:
                    if full_path.exists():
                        full_path.unlink()
                else:
                    full_path.write_text(original, encoding="utf-8")
            except OSError as exc:
                logger.error("Rollback failed for '%s': %s", path, exc)


def _hash_file(path: Path) -> str:
    """Return SHA256 hex digest of a file, or empty string if it doesn't exist."""
    if not path.exists():
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _parse_unified_diff(diff_text: str) -> dict[str, str]:
    """Parse a unified diff into a dict of path → new content.

    This is a simplified parser — in production, `patch` utility should be used.
    Returns empty dict on parse error.
    """
    import re

    result: dict[str, str] = {}
    current_file: str | None = None
    current_lines: list[str] = []

    for line in diff_text.splitlines(keepends=True):
        # Match +++ b/path or +++ path
        m = re.match(r"^\+\+\+\s+(?:b/)?(.+)$", line.rstrip("\n"))
        if m:
            if current_file and current_lines:
                result[current_file] = "".join(current_lines)
            current_file = m.group(1).strip()
            current_lines = []
            continue

        if current_file is not None:
            if line.startswith("---") or line.startswith("@@") or line.startswith("diff "):
                continue
            if line.startswith("+"):
                current_lines.append(line[1:])
            elif line.startswith(" "):
                current_lines.append(line[1:])
            # Lines starting with "-" are removed — skip them

    if current_file and current_lines:
        result[current_file] = "".join(current_lines)

    return result
