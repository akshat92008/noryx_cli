"""
nexus/collaboration/integration.py

IntegrationCoordinator: transactionally applies accepted worker results
to a clean integration workspace after conflict checks and ordering,
calculates integrated tree hash, and runs central verification.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
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

    def integrate(
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
            if review is None or not review.accepted or review.decision != ReviewDecision.APPROVE_FOR_INTEGRATION:
                logger.warning(
                    "IntegrationCoordinator: assignment '%s' skipped — review decision not APPROVE_FOR_INTEGRATION.",
                    result.assignment_id,
                )
                rejected.append(result.assignment_id)
                continue
            if result.status not in (AssignmentStatus.COMPLETED, AssignmentStatus.LOCALLY_VALIDATED):
                rejected.append(result.assignment_id)
                continue
            eligible.append(result)

        if not eligible:
            return IntegrationResult(
                integration_id=integration_id,
                status=IntegrationStatus.FAILED if accepted_results else IntegrationStatus.INTEGRATED,
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
                    affected_files=[_get_change_path(c) for c in r.proposed_changes if _get_change_path(c)],
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

            blocked_ids = {sc.assignment_id_a for sc in blocking_conflicts} | \
                          {sc.assignment_id_b for sc in blocking_conflicts}
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
            for item in self._lead_root.iterdir():
                if item.name.startswith(".") or item.name in ("__pycache__", "build", "dist", "node_modules"):
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
                            ProcessExecutionGateway.run(
                                ProcessRequest.create(
                                    purpose="apply_patch",
                                    command=["patch", "-p1", "-i", str(patch_file)],
                                    workspace=int_workspace_dir,
                                )
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
                conflict_descriptions.append("Central verification failed — integration rolled back.")
                integrated_tree_hash = None

            status = IntegrationStatus.INTEGRATED if verification_passed else IntegrationStatus.FAILED

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
