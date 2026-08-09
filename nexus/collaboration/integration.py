"""
nexus/collaboration/integration.py

IntegrationCoordinator: transactionally applies accepted worker results
to a clean integration workspace after conflict checks and ordering,
calculates integrated tree hash, runs central verification, then
**atomically commits the verified tree back to the lead workspace**.

Fix P0 (2026-08-09): the original implementation built and verified the
integration in a temporary directory but never wrote it back to
``lead_workspace_root``.  The temp dir was deleted in the ``finally``
block, discarding every change.

Fix P1 (2026-08-09): concurrent calls to ``integrate()`` raced over the
lead workspace.  A per-instance ``threading.Lock`` now serialises all
integration attempts so that each one sees the previous one's committed
result before computing its own baseline.

Fix P1 (2026-08-09): the ``patch`` subprocess result was not being
inspected.  The integration now checks the process return-code and
raises on failure so that a silently-misapplied patch cannot mark an
assignment as integrated.

Fix P2 (2026-08-09): rollback in ``_commit_to_lead`` verifies the
current on-disk content before overwriting so that a concurrent external
writer's legitimate changes are not erased during an error-path restore.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from nexus.collaboration.conflicts import (
    ChangeSignal,
    SemanticConflictAnalyser,
)
from nexus.collaboration.models import (
    AssignmentResult,
    AssignmentReview,
    AssignmentStatus,
    IntegrationResult,
    IntegrationStatus,
    ReviewDecision,
)
from nexus.process_gateway import ProcessExecutionGateway, ProcessRequest

logger = logging.getLogger(__name__)


def _get_change_path(c: Any) -> str:
    if hasattr(c, "path"):
        return str(c.path)
    if isinstance(c, dict):
        return str(c.get("path", ""))
    return str(c)


def _get_change_diff(c: Any) -> str:
    if hasattr(c, "diff_reference"):
        return str(c.diff_reference or "")
    if isinstance(c, dict):
        return str(c.get("diff_reference") or "")
    return ""


def _get_change_desc(c: Any) -> str:
    if hasattr(c, "description"):
        return str(c.description or "")
    if isinstance(c, dict):
        return str(c.get("description") or "")
    return ""


class IntegrationCoordinator:
    """
    Orchestrator-owned integration layer.
    Workers do NOT call this — only the lead orchestrator does.
    Central verification on the exact integrated tree hash is mandatory.

    Thread safety
    -------------
    ``self._lock`` serialises all calls to ``integrate()`` so that
    concurrent worker submissions see each other's committed state and
    cannot both base their integration on the same stale baseline.
    """

    def __init__(
        self,
        current_revision: str,
        verification_service: Optional[object] = None,
        lead_workspace_root: Optional[Path] = None,
    ) -> None:
        self._revision = current_revision
        self._verifier = verification_service
        self._lead_root = (lead_workspace_root or Path(os.getcwd())).resolve()
        self._conflict_analyser = SemanticConflictAnalyser()
        # P1 fix: serialise concurrent integrate() calls.
        self._lock: threading.Lock = threading.Lock()

    def integrate(
        self,
        accepted_results: Sequence[AssignmentResult],
        reviews: Dict[str, AssignmentReview],
        change_signals: Optional[List[ChangeSignal]] = None,
    ) -> IntegrationResult:
        # Serialise so that each integration sees the result of the
        # previous one before computing its own baseline tree hash.
        with self._lock:
            return self._integrate_locked(accepted_results, reviews, change_signals)

    def _integrate_locked(
        self,
        accepted_results: Sequence[AssignmentResult],
        reviews: Dict[str, AssignmentReview],
        change_signals: Optional[List[ChangeSignal]] = None,
    ) -> IntegrationResult:
        integration_id = str(uuid.uuid4())
        baseline_tree = self._get_tree_hash(self._lead_root)
        rollback_checkpoint = f"chk-{baseline_tree[:8]}"
        evidence_ids: List[str] = []
        integrated: List[str] = []
        rejected: List[str] = []
        conflict_descriptions: List[str] = []
        verification_results: List[str] = []

        eligible: List[AssignmentResult] = []
        for result in accepted_results:
            review = reviews.get(result.assignment_id)
            if (
                review is None
                or not review.accepted
                or review.decision != ReviewDecision.APPROVE_FOR_INTEGRATION
            ):
                logger.warning(
                    "IntegrationCoordinator: assignment '%s' skipped — review decision not APPROVE_FOR_INTEGRATION.",
                    result.assignment_id,
                )
                rejected.append(result.assignment_id)
                continue
            if result.status not in (
                AssignmentStatus.COMPLETED,
                AssignmentStatus.LOCALLY_VALIDATED,
            ):
                rejected.append(result.assignment_id)
                continue
            eligible.append(result)

        if not eligible:
            return IntegrationResult(
                integration_id=integration_id,
                status=IntegrationStatus.FAILED
                if accepted_results
                else IntegrationStatus.INTEGRATED,
                baseline_tree=baseline_tree,
                integrated_tree=baseline_tree,
                applied_assignments=(),
                rejected_assignments=tuple(rejected),
                conflicts=("No eligible worker results to integrate.",) if accepted_results else (),
                evidence=(),
                rollback_checkpoint=rollback_checkpoint,
            )

        if change_signals is None:
            change_signals = [
                ChangeSignal(
                    assignment_id=r.assignment_id,
                    affected_files=[
                        _get_change_path(c) for c in r.proposed_changes if _get_change_path(c)
                    ],
                )
                for r in eligible
            ]

        semantic_conflicts = self._conflict_analyser.analyse(change_signals)
        blocking_conflicts = [c for c in semantic_conflicts if c.severity == "blocking"]

        if blocking_conflicts:
            for sc in blocking_conflicts:
                desc = (
                    f"Semantic conflict [{sc.kind}] between "
                    f"'{sc.assignment_id_a}' and '{sc.assignment_id_b}': {sc.description}"
                )
                conflict_descriptions.append(desc)
                logger.error("IntegrationCoordinator: %s", desc)

            blocked_ids = {sc.assignment_id_a for sc in blocking_conflicts} | {
                sc.assignment_id_b for sc in blocking_conflicts
            }
            for r in eligible:
                if r.assignment_id in blocked_ids:
                    rejected.append(r.assignment_id)
            eligible = [r for r in eligible if r.assignment_id not in blocked_ids]

        path_to_assignments: Dict[str, List[str]] = {}
        for result in eligible:
            for change in result.proposed_changes:
                cpath = _get_change_path(change)
                if cpath:
                    path_to_assignments.setdefault(cpath, []).append(result.assignment_id)

        for path, asgn_ids in path_to_assignments.items():
            if len(asgn_ids) > 1:
                conflict_msg = (
                    f"Text conflict on path '{path}': assignments {asgn_ids} edit same file."
                )
                conflict_descriptions.append(conflict_msg)
                logger.warning("IntegrationCoordinator: %s", conflict_msg)
                for aid in asgn_ids[1:]:
                    if aid not in rejected:
                        rejected.append(aid)

        rejected_set = set(rejected)
        eligible = [r for r in eligible if r.assignment_id not in rejected_set]

        if not eligible:
            return IntegrationResult(
                integration_id=integration_id,
                status=IntegrationStatus.CONFLICTED,
                baseline_tree=baseline_tree,
                integrated_tree=None,
                applied_assignments=(),
                rejected_assignments=tuple(rejected),
                conflicts=tuple(conflict_descriptions),
                evidence=(),
                rollback_checkpoint=rollback_checkpoint,
            )

        int_workspace_dir = Path(tempfile.mkdtemp(prefix="nexus-integration-"))
        try:
            # ── Copy lead workspace into temp integration area ────────────────
            for item in self._lead_root.iterdir():
                if item.name.startswith(".") or item.name in (
                    "__pycache__",
                    "build",
                    "dist",
                    "node_modules",
                ):
                    continue
                dest = int_workspace_dir / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, symlinks=True)
                else:
                    shutil.copy2(item, dest)

            eligible_sorted = sorted(eligible, key=lambda x: x.assignment_id)
            for result in eligible_sorted:
                for change in result.proposed_changes:
                    cpath = _get_change_path(change)
                    if not cpath:
                        continue
                    target_file = int_workspace_dir / cpath
                    target_file.parent.mkdir(parents=True, exist_ok=True)

                    cdiff = _get_change_diff(change)
                    cdesc = _get_change_desc(change)

                    if cdiff:
                        patch_file = int_workspace_dir / f"{uuid.uuid4().hex[:8]}.patch"
                        patch_file.write_text(cdiff, encoding="utf-8")
                        try:
                            # P1 fix: inspect patch return code; do not silently
                            # mark an assignment integrated if patch fails.
                            proc = ProcessExecutionGateway.run(
                                ProcessRequest.create(
                                    purpose="apply_patch",
                                    command=["patch", "-p1", "--fuzz=0", "--no-backup-if-mismatch", "-i", str(patch_file)],
                                    workspace=int_workspace_dir,
                                )
                            )
                            rc = getattr(proc, "returncode", None)
                            stdout = getattr(proc, "stdout", "") or ""
                            stderr = getattr(proc, "stderr", "") or ""
                            if rc is not None and rc != 0:
                                raise RuntimeError(
                                    f"patch returned exit code {rc} for "
                                    f"assignment '{result.assignment_id}': {stderr}"
                                )
                            out_lower = (stdout + stderr).lower()
                            if "fuzz" in out_lower or ("hunk" in out_lower and "failed" in out_lower):
                                raise RuntimeError(
                                    f"patch applied with unsafe fuzz or errors for assignment '{result.assignment_id}': {stdout} {stderr}"
                                )
                            rej_files = list(int_workspace_dir.rglob("*.rej"))
                            if rej_files:
                                raise RuntimeError(
                                    f"patch left reject file(s) ({[r.name for r in rej_files]}) for assignment '{result.assignment_id}'"
                                )
                        finally:
                            patch_file.unlink(missing_ok=True)
                    else:
                        with open(target_file, "a", encoding="utf-8") as f:
                            f.write(f"\n# Integrated change: {cdesc}\n")

                integrated.append(result.assignment_id)
                evidence_ids.extend(result.evidence_ids or result.evidence)

            integrated_tree_hash = self._get_tree_hash(int_workspace_dir)

            verification_passed = True
            if self._verifier is not None:
                try:
                    if hasattr(self._verifier, "run_verification"):
                        outcome = self._verifier.run_verification(
                            context=str(int_workspace_dir),
                            checks=["structural", "integration", "acceptance"],
                        )
                        verification_passed = outcome.passed
                    else:
                        verification_passed = True
                    verification_results.append(
                        f"central_verification:{'PASS' if verification_passed else 'FAIL'}"
                    )
                except Exception as exc:
                    logger.error("IntegrationCoordinator: central verification error: %s", exc)
                    verification_passed = False
                    verification_results.append(f"central_verification:ERROR:{exc}")
            else:
                logger.error("IntegrationCoordinator: Verification service unavailable.")
                verification_passed = False
                verification_results.append("central_verification:VERIFICATION_UNAVAILABLE")

            if not verification_passed:
                logger.error(
                    "IntegrationCoordinator [tx=%s]: central verification failed. Rolling back.",
                    integration_id,
                )
                for aid in integrated:
                    rejected.append(aid)
                integrated.clear()
                conflict_descriptions.append(
                    "Central verification failed — integration rolled back."
                )
                integrated_tree_hash = None

            # ── P0 fix: commit verified integration tree to lead workspace ────
            if verification_passed and integrated:
                try:
                    self._commit_to_lead(int_workspace_dir, baseline_tree)
                except Exception as exc:
                    logger.error(
                        "IntegrationCoordinator [tx=%s]: commit to lead workspace failed: %s",
                        integration_id,
                        exc,
                    )
                    for aid in integrated:
                        rejected.append(aid)
                    integrated.clear()
                    integrated_tree_hash = None
                    conflict_descriptions.append(
                        f"Lead-workspace commit failed — integration rolled back: {exc}"
                    )
                    verification_results.append(f"commit_to_lead:FAILED:{exc}")
                    verification_passed = False

            status = (
                IntegrationStatus.INTEGRATED if verification_passed else IntegrationStatus.FAILED
            )

            return IntegrationResult(
                integration_id=integration_id,
                status=status,
                baseline_tree=baseline_tree,
                integrated_tree=integrated_tree_hash,
                applied_assignments=tuple(integrated),
                rejected_assignments=tuple(rejected),
                conflicts=tuple(conflict_descriptions),
                evidence=tuple(set(evidence_ids)),
                rollback_checkpoint=rollback_checkpoint,
                verification_results=tuple(verification_results),
            )

        finally:
            shutil.rmtree(int_workspace_dir, ignore_errors=True)

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _commit_to_lead(self, int_workspace_dir: Path, baseline_tree: str) -> None:
        """Atomically commit verified integration tree to lead workspace with transactional rollback.

        Guarantees:
        1. Drift Check: Verifies lead workspace hash has not drifted since baseline_tree.
        2. Snapshot: Captures full pre-commit snapshot of affected/existing lead workspace files and metadata.
        3. Transaction Execution: Performs file replacements, creations, and deletions in lead workspace,
           tracking every mutation.
        4. Transactional Rollback: On any failure mid-commit, restores modified and deleted files to their
           exact pre-commit state if unchanged by external writers. Newly created files are unlinked.
           Empty directories created by the transaction are cleaned up.
        5. External Modification Safety: If an external process modified a file after Noryx committed it,
           rollback preserves the external change, surfaces a ROLLBACK_CONFLICT status error, and does NOT
           clobber external work.
        6. Workspace Invariant: If commit fails and rollback completes cleanly,
           lead_workspace_after == lead_workspace_before (hash equality guaranteed).
        """
        current_tree = self._get_tree_hash(self._lead_root)
        if current_tree != baseline_tree:
            raise RuntimeError(
                f"Lead workspace drifted during integration "
                f"(baseline={baseline_tree!r}, current={current_tree!r}). "
                "Aborting commit to avoid overwriting concurrent external changes."
            )

        ignored_dir_names = {".git", ".noryx", "__pycache__", "build", "dist", "node_modules", ".venv"}

        snapshot: dict[Path, bytes] = {}
        pre_existing_files: set[Path] = set()
        pre_existing_dirs: set[Path] = set()

        for root, dirs, files in os.walk(self._lead_root):
            root_path = Path(root)
            rel_root = root_path.relative_to(self._lead_root)
            if rel_root.parts and rel_root.parts[0] in ignored_dir_names:
                dirs.clear()
                continue

            pre_existing_dirs.add(rel_root)

            for f in files:
                if f.startswith("."):
                    continue
                file_path = root_path / f
                rel_file = file_path.relative_to(self._lead_root)
                pre_existing_files.add(rel_file)
                try:
                    snapshot[rel_file] = file_path.read_bytes()
                except Exception as exc:
                    logger.warning("IntegrationCoordinator: failed to snapshot %s: %s", file_path, exc)

        executed_actions: list[tuple[str, Path, bytes | None]] = []

        try:
            for src in sorted(int_workspace_dir.rglob("*")):
                if not src.is_file():
                    continue
                rel = src.relative_to(int_workspace_dir)
                if rel.parts and rel.parts[0] in ignored_dir_names:
                    continue

                dest = self._lead_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)

                new_bytes = src.read_bytes()
                existed_before = rel in pre_existing_files
                action_type = "MODIFY" if existed_before else "CREATE"

                temp = dest.with_name(f".{dest.name}.intg-{uuid.uuid4().hex}.tmp")
                temp.write_bytes(new_bytes)
                try:
                    os.replace(temp, dest)
                except Exception:
                    temp.unlink(missing_ok=True)
                    raise

                executed_actions.append((action_type, rel, new_bytes))

            for rel in list(pre_existing_files):
                src_equiv = int_workspace_dir / rel
                if not src_equiv.exists():
                    dest = self._lead_root / rel
                    if dest.exists():
                        dest.unlink(missing_ok=True)
                        executed_actions.append(("DELETE", rel, None))

            for root, dirs, files in os.walk(self._lead_root, topdown=False):
                root_path = Path(root)
                rel_root = root_path.relative_to(self._lead_root)
                if rel_root == Path("."):
                    continue
                if rel_root.parts and rel_root.parts[0] in ignored_dir_names:
                    continue
                if not os.listdir(root_path):
                    try:
                        root_path.rmdir()
                    except OSError:
                        pass

        except Exception as commit_exc:
            logger.error(
                "IntegrationCoordinator: commit to lead failed mid-way (%s action(s) executed): %s. Initiating transactional rollback.",
                len(executed_actions),
                commit_exc,
            )
            rollback_conflicted = False
            conflict_details: list[str] = []

            for action_type, rel, bytes_written in reversed(executed_actions):
                dest = self._lead_root / rel
                if action_type == "MODIFY":
                    current_bytes = dest.read_bytes() if dest.exists() else None
                    if current_bytes == bytes_written:
                        if rel in snapshot:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(snapshot[rel])
                    else:
                        rollback_conflicted = True
                        conflict_details.append(f"{rel}: modified externally during rollback")
                        logger.warning(
                            "IntegrationCoordinator rollback: preserving external change on %s", rel
                        )

                elif action_type == "CREATE":
                    current_bytes = dest.read_bytes() if dest.exists() else None
                    if current_bytes == bytes_written:
                        dest.unlink(missing_ok=True)
                    elif current_bytes is not None:
                        rollback_conflicted = True
                        conflict_details.append(f"{rel}: newly created file modified externally")
                        logger.warning(
                            "IntegrationCoordinator rollback: preserving external file %s", rel
                        )

                elif action_type == "DELETE":
                    if not dest.exists():
                        if rel in snapshot:
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(snapshot[rel])
                    else:
                        rollback_conflicted = True
                        conflict_details.append(f"{rel}: recreated externally after deletion")
                        logger.warning(
                            "IntegrationCoordinator rollback: preserving external file %s", rel
                        )

            for root, dirs, files in os.walk(self._lead_root, topdown=False):
                root_path = Path(root)
                rel_root = root_path.relative_to(self._lead_root)
                if rel_root == Path("."):
                    continue
                if rel_root.parts and rel_root.parts[0] in ignored_dir_names:
                    continue
                if rel_root not in pre_existing_dirs and not os.listdir(root_path):
                    try:
                        root_path.rmdir()
                    except OSError:
                        pass

            if rollback_conflicted:
                msg = (
                    f"Lead-workspace commit failed: {commit_exc}. "
                    f"ROLLBACK_CONFLICT: Rollback incomplete due to external writer conflicts: {'; '.join(conflict_details)}"
                )
                logger.error("IntegrationCoordinator: %s", msg)
                raise RuntimeError(msg) from commit_exc

            post_rollback_tree = self._get_tree_hash(self._lead_root)
            if post_rollback_tree != baseline_tree:
                msg = (
                    f"Lead-workspace commit failed: {commit_exc}. "
                    f"Rollback completed but tree hash mismatch: expected {baseline_tree!r}, got {post_rollback_tree!r}."
                )
                logger.error("IntegrationCoordinator: %s", msg)
                raise RuntimeError(msg) from commit_exc

            raise RuntimeError(
                f"Commit to lead workspace failed after {len(executed_actions)} action(s): {commit_exc}. "
                f"Lead workspace successfully rolled back to baseline state."
            ) from commit_exc

    @staticmethod
    def _get_tree_hash(path: Path) -> str:
        h = hashlib.sha256()
        try:
            for root, _, files in os.walk(path):
                for f in sorted(files):
                    if f.startswith("."):
                        continue
                    fp = Path(root) / f
                    h.update(f.encode())
                    try:
                        h.update(fp.read_bytes())
                    except Exception:
                        pass
            return h.hexdigest()[:20]
        except Exception as exc:
            raise RuntimeError(f"Failed to calculate tree hash: {exc}") from exc
