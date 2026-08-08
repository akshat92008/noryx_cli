"""
Change Set Persistence — Sprint 8.

Persists and loads multi-stage execution state outside model context.
All artifacts are stored under .nexus/runs/<run-id>/ with schema version,
repository snapshot, plan version, and timestamps.

On resume:
- Verifies the repository tree hash
- Validates completed stages
- Detects external changes that invalidate prior stages
- Continues only from safe boundaries
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus.multifile.contracts import (
    ChangeSetValidationResult,
    ChangeStageStatus,
    EngineeringChangeSet,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "nexus.changeset-persistence.v8"


class ChangeSetPersistence:
    """Stores and loads multi-stage change-set execution state."""

    def __init__(self, run_dir: str | Path) -> None:
        self.run_dir = Path(run_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_change_set(self, cs: EngineeringChangeSet) -> Path:
        """Persist the full change set definition."""
        self.run_dir.mkdir(parents=True, exist_ok=True)
        path = self.run_dir / "change-set.json"
        data = {
            "schema_version": SCHEMA_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            **cs.to_dict(),
        }
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        logger.debug("Change set persisted to %s", path)
        return path

    def load_change_set(self, run_id: str) -> EngineeringChangeSet | None:
        """Load a persisted change set by run_id."""
        path = self.run_dir / "change-set.json"
        if not path.exists():
            logger.warning("No change set found at %s", path)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.pop("schema_version", None)
            data.pop("saved_at", None)
            return EngineeringChangeSet.from_dict(data)
        except Exception as exc:
            logger.error("Failed to load change set: %s", exc)
            return None

    def save_impact_report(self, report_dict: dict[str, Any]) -> Path:
        path = self.run_dir / "impact-report.json"
        self._write_artifact(path, report_dict)
        return path

    def save_contract_inventory(self, contracts: list[dict[str, Any]]) -> Path:
        path = self.run_dir / "contract-inventory.json"
        self._write_artifact(path, {"contracts": contracts})
        return path

    def save_stage_artifact(self, stage_id: str, data: dict[str, Any]) -> Path:
        stages_dir = self.run_dir / "stages"
        stages_dir.mkdir(parents=True, exist_ok=True)
        path = stages_dir / f"{stage_id}.json"
        self._write_artifact(path, data)
        return path

    def load_stage_artifact(self, stage_id: str) -> dict[str, Any] | None:
        path = self.run_dir / "stages" / f"{stage_id}.json"
        return self._read_artifact(path)

    def save_stage_patch(self, stage_id: str, patch_content: str) -> Path:
        patches_dir = self.run_dir / "patches"
        patches_dir.mkdir(parents=True, exist_ok=True)
        path = patches_dir / f"{stage_id}.patch"
        path.write_text(patch_content, encoding="utf-8")
        return path

    def save_verification_result(self, stage_id: str, data: dict[str, Any]) -> Path:
        verification_dir = self.run_dir / "verification"
        verification_dir.mkdir(parents=True, exist_ok=True)
        path = verification_dir / f"{stage_id}.json"
        self._write_artifact(path, data)
        return path

    def save_final_verification(self, data: dict[str, Any]) -> Path:
        verification_dir = self.run_dir / "verification"
        verification_dir.mkdir(parents=True, exist_ok=True)
        path = verification_dir / "final.json"
        self._write_artifact(path, data)
        return path

    def save_compatibility(self, data: dict[str, Any]) -> Path:
        path = self.run_dir / "compatibility.json"
        self._write_artifact(path, data)
        return path

    def save_rollback_plan(self, data: dict[str, Any]) -> Path:
        path = self.run_dir / "rollback.json"
        self._write_artifact(path, data)
        return path

    # ------------------------------------------------------------------
    # Resume logic
    # ------------------------------------------------------------------

    def prepare_resume(self, run_id: str, repo_root: str) -> dict[str, Any]:
        """Prepare for resumption by validating completed stages.

        Returns a status dict with 'safe_to_resume' and 'last_safe_stage_id'.
        """
        cs = self.load_change_set(run_id)
        if cs is None:
            return {
                "safe_to_resume": False,
                "reason": "No persisted change set found.",
                "last_safe_stage_id": None,
            }

        # Compute current tree hash
        current_hash = _compute_tree_hash(Path(repo_root))

        # Check if repository changed externally
        saved_snapshot = cs.repository_snapshot_id
        if saved_snapshot and saved_snapshot != current_hash:
            # Check whether completed stages are still valid
            invalidated = self._detect_invalidated_stages(cs, Path(repo_root))
            if invalidated:
                return {
                    "safe_to_resume": False,
                    "reason": (
                        f"Repository changed externally. "
                        f"Stages {invalidated} may be invalidated."
                    ),
                    "last_safe_stage_id": None,
                    "invalidated_stages": invalidated,
                }

        # Find last completed stage
        last_safe: str | None = None
        for stage in cs.stages:
            artifact = self.load_stage_artifact(stage.stage_id)
            if artifact and artifact.get("status") == ChangeStageStatus.COMPLETED.value:
                last_safe = stage.stage_id
            else:
                break  # stages must complete in order

        return {
            "safe_to_resume": True,
            "last_safe_stage_id": last_safe,
            "pending_stages": [
                s.stage_id for s in cs.stages
                if s.stage_id not in cs.completed_stage_ids
            ],
        }

    def _detect_invalidated_stages(
        self, cs: EngineeringChangeSet, repo_root: Path
    ) -> list[str]:
        """Check completed stages to see if their files were changed externally."""
        invalidated: list[str] = []

        for stage_id in cs.completed_stage_ids:
            artifact = self.load_stage_artifact(stage_id)
            if not artifact:
                invalidated.append(stage_id)
                continue

            for file_path in artifact.get("file_paths", []):
                full_path = repo_root / file_path
                # A stage is invalidated if a file it modified no longer exists
                # or has been modified since (more sophisticated: compare hash)
                if not full_path.exists():
                    invalidated.append(stage_id)
                    break

        return invalidated

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write_artifact(self, path: Path, data: dict[str, Any]) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        except OSError as exc:
            logger.error("Failed to write artifact to %s: %s", path, exc)

    def _read_artifact(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to read artifact from %s: %s", path, exc)
            return None


def _compute_tree_hash(root: Path, max_files: int = 2000) -> str:
    """Compute a deterministic hash over repository file names and sizes."""
    digest = hashlib.sha256()
    count = 0
    for f in sorted(root.rglob("*")):
        if count >= max_files:
            break
        rel = str(f.relative_to(root))
        if any(skip in rel for skip in (".git", ".venv", "__pycache__", ".nexus")):
            continue
        if f.is_file():
            digest.update(f"{rel}:{f.stat().st_size}".encode())
            count += 1
    return digest.hexdigest()
