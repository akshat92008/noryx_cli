from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus.intelligence.engineering import (
    EngineeringBrain,
    EngineeringMemoryStore,
    FailureLearningIntegrityError,
    FailureLearningStore,
    LongHorizonController,
    LongHorizonIntegrityError,
    LongHorizonPhase,
    MemoryConflictError,
    MemoryIntegrityError,
    ScopeEvidenceType,
    ScopeExpansionEvidence,
    SemanticVerifier,
    SurgicalScopeGuard,
)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "service.py").write_text(
        "def calculate_total(items):\n    return sum(items)\n",
        encoding="utf-8",
    )
    (root / "test_service.py").write_text(
        "from service import calculate_total\n\ndef test_total():\n    assert calculate_total([1, 2]) == 3\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text("Example repository\n", encoding="utf-8")
    return root


def test_engineering_memory_detects_tampering(tmp_path: Path):
    root = _repo(tmp_path)
    store = EngineeringMemoryStore(root)
    memory = store.create("task-1", "Fix calculate_total", decisive_files=["service.py"])
    loaded = store.load("task-1")
    assert loaded.objective == memory.objective

    path = store.path_for("task-1")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["objective"] = "silently changed"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(MemoryIntegrityError):
        store.load("task-1")


def test_scope_guard_blocks_prohibited_and_requires_expansion_reason(tmp_path: Path):
    root = _repo(tmp_path)
    guard = SurgicalScopeGuard.from_repository_context(
        root,
        objective="Fix service.py without changing README.md",
        decisive_files=["service.py"],
        related_tests=["test_service.py"],
        task_type="bug_repair",
        confidence=0.9,
        strict=True,
    )
    assert guard.authorize(["service.py"]).allowed
    assert not guard.authorize(["README.md"]).allowed

    (root / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    denied = guard.authorize(["helper.py"])
    assert not denied.allowed
    approved = guard.authorize(
        ["helper.py"],
        expansion_evidence=[
            ScopeExpansionEvidence(
                evidence_type=ScopeEvidenceType.IMPORT_EDGE,
                target_path="helper.py",
                source_path="service.py",
                evidence_id="edge-1",
                source_revision="revision-1",
                symbol="VALUE",
            )
        ],
    )
    assert approved.allowed
    assert approved.requires_scope_expansion


def test_failure_learning_is_hash_chained_and_escalates(tmp_path: Path):
    root = _repo(tmp_path)
    store = FailureLearningStore(root)
    first = store.record(category="verification", phase="verification", summary="test_add failed at line 12")
    second = store.record(category="verification", phase="verification", summary="test_add failed at line 99")
    third = store.record(category="verification", phase="verification", summary="test_add failed at line 7")
    assert first.occurrence == 1
    assert second.occurrence == 2
    assert third.occurrence == 3
    assert "escalate" in third.recommended_strategy
    assert store.verify_chain()


def test_long_horizon_requires_evidence_for_verification(tmp_path: Path):
    root = _repo(tmp_path)
    controller = LongHorizonController(root, "task-long", "Fix total")
    controller.transition(LongHorizonPhase.PLAN, summary="context mapped")
    controller.transition(LongHorizonPhase.IMPLEMENT, summary="plan approved")
    with pytest.raises(ValueError):
        controller.transition(LongHorizonPhase.VERIFY, summary="tests pass")
    controller.transition(
        LongHorizonPhase.VERIFY,
        summary="tests pass",
        evidence_ids=["ev-1"],
    )
    controller.transition(
        LongHorizonPhase.REVIEW,
        summary="review approved",
        evidence_ids=["ev-2"],
    )
    controller.transition(
        LongHorizonPhase.COMPLETE,
        summary="semantic acceptance passed",
        evidence_ids=["ev-3"],
    )
    resumed = LongHorizonController(root, "task-long", "Fix total")
    assert resumed.state.phase == LongHorizonPhase.COMPLETE


def test_semantic_verifier_rejects_tests_only_and_scope_expansion():
    verifier = SemanticVerifier()
    result = verifier.verify(
        objective="Fix production behavior",
        task_type="bug_repair",
        evidence=[
            {"id": "m1", "kind": "file_mutation", "status": "verified"},
            {"id": "v1", "kind": "verification_check", "status": "verified"},
            {"id": "r1", "kind": "independent_review", "status": "verified"},
        ],
        changed_files=["tests/test_service.py", "tests/unplanned.py"],
        allowed_files=["service.py", "tests/test_service.py"],
        prohibited_patterns=[],
        review_required=True,
    )
    assert not result.satisfied
    codes = {item.code for item in result.findings}
    assert "SEM-SCOPE-EXPANSION" in codes
    assert "SEM-TESTS-ONLY" in codes


def test_engineering_brain_builds_repository_aware_contract(tmp_path: Path):
    root = _repo(tmp_path)
    brain = EngineeringBrain(root)
    contract = brain.prepare(
        "Fix calculate_total in service.py and verify test_service.py without changing README.md",
        task_id="task-brain",
        strict=True,
    )
    assert "service.py" in contract.decisive_files
    assert "README.md" not in contract.decisive_files
    assert any(
        item["kind"] == "FORBID_FILE_WRITE" and item["target"] == "README.md"
        for item in contract.scope_contract["constraints"]
    )
    assert contract.repository_tree_hash
    assert contract.task_type == "bug_repair"
    assert contract.memory_path.endswith("task-brain.json")
    assert contract.deliberation["hypotheses"]
    assert contract.completion_contract["obligations"]
    assert brain.deliberation is not None
    assert brain.completion_contract is not None
    assert "NEXUS ENGINEERING BRAIN" in brain.prompt_context()
    assert "model assertion is not evidence" in brain.prompt_context().lower()
    assert "README.md" not in contract.completion_contract["required_change_files"]


def test_failure_learning_refuses_corrupt_suffix(tmp_path: Path):
    root = _repo(tmp_path)
    store = FailureLearningStore(root)
    store.record(category="verification", phase="verification", summary="first failure")
    with store.path.open("a", encoding="utf-8") as handle:
        handle.write("{corrupt\n")
    assert not store.verify_chain()
    with pytest.raises(FailureLearningIntegrityError):
        store.record(category="verification", phase="verification", summary="second failure")


def test_task_memory_rejects_stale_writer(tmp_path: Path):
    root = _repo(tmp_path)
    store = EngineeringMemoryStore(root)
    store.create("task-race", "Fix service")
    first = store.load("task-race")
    stale = store.load("task-race")
    first.status = "VERIFYING"
    store.save(first)
    stale.status = "STALE"
    with pytest.raises(MemoryConflictError):
        store.save(stale)


def test_long_horizon_refuses_tampered_checkpoint(tmp_path: Path):
    root = _repo(tmp_path)
    controller = LongHorizonController(root, "task-tamper", "Fix total")
    data = json.loads(controller.path.read_text(encoding="utf-8"))
    data["phase"] = "COMPLETE"
    controller.path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(LongHorizonIntegrityError):
        LongHorizonController(root, "task-tamper", "Fix total")


def test_failure_learning_rejects_recomputed_unkeyed_hash(tmp_path: Path):
    import hashlib
    import json

    root = _repo(tmp_path)
    store = FailureLearningStore(root)
    store.record(category="verification", phase="verification", summary="first failure")
    data = json.loads(store.path.read_text(encoding="utf-8").splitlines()[0])
    data["summary"] = "forged lesson"
    body = dict(data)
    body.pop("record_hash")
    data["record_hash"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    store.path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    assert not store.verify_chain()
    with pytest.raises(FailureLearningIntegrityError):
        store.recent_context()
