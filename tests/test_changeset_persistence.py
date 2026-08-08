"""Tests for ChangeSetPersistence (Sprint 8)."""
from __future__ import annotations

import pytest
from pathlib import Path

from nexus.multifile.contracts import (
    ChangeStage,
    ChangeStageStatus,
    ChangeType,
    EngineeringChangeSet,
    PlannedFileChange,
    TaskType,
)
from nexus.multifile.persistence import ChangeSetPersistence


def test_save_and_load_change_set(tmp_path: Path):
    persistence = ChangeSetPersistence(tmp_path)
    cs = EngineeringChangeSet(
        run_id="run-100",
        task_type=TaskType.FEATURE,
        objective="Multi-file persistence test",
        file_changes=[
            PlannedFileChange(path="nexus/a.py", reason="Initial edit", change_type=ChangeType.MODIFY)
        ],
    )
    save_path = persistence.save_change_set(cs)
    assert save_path.exists()

    loaded = persistence.load_change_set("run-100")
    assert loaded is not None
    assert loaded.run_id == "run-100"
    assert loaded.objective == "Multi-file persistence test"
    assert len(loaded.file_changes) == 1


def test_save_and_load_stage_artifact(tmp_path: Path):
    persistence = ChangeSetPersistence(tmp_path)
    stage_data = {
        "stage_id": "stage-1",
        "status": ChangeStageStatus.COMPLETED.value,
        "file_paths": ["nexus/a.py"],
    }
    artifact_path = persistence.save_stage_artifact("stage-1", stage_data)
    assert artifact_path.exists()

    loaded = persistence.load_stage_artifact("stage-1")
    assert loaded is not None
    assert loaded["stage_id"] == "stage-1"
    assert loaded["status"] == ChangeStageStatus.COMPLETED.value


def test_prepare_resume_valid(tmp_path: Path):
    (tmp_path / "nexus").mkdir(parents=True, exist_ok=True)
    (tmp_path / "nexus" / "a.py").write_text("content", encoding="utf-8")

    persistence = ChangeSetPersistence(tmp_path)
    cs = EngineeringChangeSet(
        run_id="run-200",
        completed_stage_ids=["stage-1"],
        stages=[
            ChangeStage(
                stage_id="stage-1",
                name="S1",
                description="Stage 1",
                file_paths=["nexus/a.py"],
                status=ChangeStageStatus.COMPLETED,
            ),
            ChangeStage(
                stage_id="stage-2",
                name="S2",
                description="Stage 2",
                file_paths=["nexus/b.py"],
                status=ChangeStageStatus.PENDING,
            ),
        ],
    )
    persistence.save_change_set(cs)
    persistence.save_stage_artifact(
        "stage-1",
        {"stage_id": "stage-1", "status": ChangeStageStatus.COMPLETED.value, "file_paths": ["nexus/a.py"]},
    )

    resume_info = persistence.prepare_resume("run-200", str(tmp_path))
    assert resume_info["safe_to_resume"] is True
    assert resume_info["last_safe_stage_id"] == "stage-1"
    assert "stage-2" in resume_info["pending_stages"]
