from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from nexus.intelligence.engineering import (
    ConstraintCompiler,
    EngineeringBrain,
    EngineeringMemoryStore,
    MemoryIntegrityError,
    ScopeEvidenceType,
    ScopeExpansionEvidence,
    SemanticVerifier,
    SurgicalScopeGuard,
)
from nexus.intelligence.repository.engine import RepositoryIntelligence
from nexus.intelligence.repository.snapshot import workspace_revision
from nexus.performance import ContentHashCache


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calculator.py").write_text(
        "def multiply(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (root / "verify.py").write_text(
        "from calculator import multiply\n\ndef test_multiply():\n    assert multiply(2, 3) == 6\n",
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("objective", "kind", "target"),
    [
        ("Fix calculator.py without changing verify.py", "FORBID_FILE_WRITE", "verify.py"),
        ("Fix calculator.py; do not modify verify.py", "FORBID_FILE_WRITE", "verify.py"),
        ("Fix calculator.py and leave verify.py untouched", "FORBID_FILE_WRITE", "verify.py"),
        ("Fix the bug but preserve the database schema", "FORBID_SCHEMA_CHANGE", ""),
        ("Fix the bug with no new dependencies", "FORBID_NEW_DEPENDENCY", ""),
        ("Fix the endpoint and keep the public API unchanged", "FORBID_PUBLIC_API_CHANGE", ""),
        ("Fix internals while maintaining backward compatibility", "REQUIRE_BACKWARD_COMPATIBILITY", ""),
    ],
)
def test_constraint_compiler_emits_typed_hard_policy(objective: str, kind: str, target: str):
    compiled = ConstraintCompiler.compile(objective)
    assert any(
        item.kind.value == kind and (not target or item.target == target)
        for item in compiled.constraints
    )


def test_prohibited_explicit_file_is_removed_from_decisive_scope(tmp_path: Path):
    root = _repo(tmp_path)
    brain = EngineeringBrain(root)
    contract = brain.prepare(
        "Fix calculator.py multiplication without changing verify.py",
        task_id="prohibition",
        strict=True,
    )
    assert "calculator.py" in contract.decisive_files
    assert "verify.py" not in contract.decisive_files
    assert not brain.authorize_mutation(["verify.py"]).allowed


def test_free_form_scope_reason_cannot_self_authorize(tmp_path: Path):
    root = _repo(tmp_path)
    (root / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    guard = SurgicalScopeGuard.from_repository_context(
        root,
        objective="Fix calculator.py",
        decisive_files=["calculator.py"],
        related_tests=[],
        task_type="bug_repair",
        confidence=0.95,
        strict=True,
    )
    decision = guard.authorize(["helper.py"], reason="model says this is needed")
    assert not decision.allowed
    assert "evidence" in decision.reason.lower()


def test_typed_scope_evidence_is_target_specific(tmp_path: Path):
    root = _repo(tmp_path)
    (root / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    guard = SurgicalScopeGuard.from_repository_context(
        root,
        objective="Fix calculator.py",
        decisive_files=["calculator.py"],
        related_tests=[],
        task_type="bug_repair",
        confidence=0.95,
        strict=True,
    )
    wrong_target = ScopeExpansionEvidence(
        evidence_type=ScopeEvidenceType.IMPORT_EDGE,
        target_path="other.py",
        source_path="calculator.py",
        evidence_id="edge-1",
        source_revision="revision-1",
    )
    assert not guard.authorize(["helper.py"], expansion_evidence=[wrong_target]).allowed


def test_semantic_verifier_rejects_model_prose_as_criterion_evidence():
    result = SemanticVerifier().verify(
        objective="Add password reset",
        task_type="feature_implementation",
        evidence=[
            {"id": "m1", "kind": "file_mutation", "status": "verified"},
            {
                "id": "v1",
                "kind": "verification_check",
                "status": "verified",
                "tool": "pytest",
                "command": "pytest -q",
                "exit_code": 0,
                "metadata": {"check_type": "test", "independently_validated": True},
            },
            {"id": "r1", "kind": "independent_review", "status": "verified"},
            {
                "id": "note-1",
                "kind": "note",
                "status": "verified",
                "claim": "Users can reset passwords",
                "raw_output": "Users can reset passwords",
            },
        ],
        changed_files=["service.py"],
        allowed_files=["service.py"],
        prohibited_patterns=[],
        acceptance_criteria=["Users can reset passwords"],
    )
    assert not result.satisfied
    assert result.requirement_results["Users can reset passwords"] == "UNVERIFIED"


def test_semantic_verifier_accepts_explicit_criterion_mapping():
    criterion = "Users can reset passwords"
    result = SemanticVerifier().verify(
        objective="Add password reset",
        task_type="feature_implementation",
        evidence=[
            {"id": "m1", "kind": "file_mutation", "status": "verified"},
            {
                "id": "v1",
                "kind": "behavioral_check",
                "status": "verified",
                "tool": "http_probe",
                "command": "POST /reset",
                "exit_code": 0,
                "metadata": {
                    "criterion_ids": [criterion],
                    "independently_validated": True,
                },
            },
            {"id": "r1", "kind": "independent_review", "status": "verified"},
        ],
        changed_files=["service.py"],
        allowed_files=["service.py"],
        prohibited_patterns=[],
        acceptance_criteria=[criterion],
    )
    assert result.satisfied
    assert result.requirement_evidence[criterion] == ["v1"]


def test_content_cache_never_returns_same_size_stale_content(tmp_path: Path):
    cache = ContentHashCache(tmp_path / "cache", parser_version="v2")
    source = tmp_path / "same_size.py"
    stale = 0
    for index in range(150):
        first = f"a={index % 10}\n"
        second = f"b={index % 10}\n"
        assert len(first) == len(second)
        source.write_text(first, encoding="utf-8")
        cache.put(source, {"symbol": "a"})
        source.write_text(second, encoding="utf-8")
        if cache.get(source) is not None:
            stale += 1
    assert stale == 0


def test_repository_intelligence_invalidates_same_size_rewrite(tmp_path: Path):
    root = _repo(tmp_path)
    intelligence = RepositoryIntelligence(root, state_root=tmp_path / "state")
    intelligence.build()
    before = intelligence.files["calculator.py"].content_hash
    (root / "calculator.py").write_text(
        "def multiply(a, b):\n    return a * b\n", encoding="utf-8"
    )
    intelligence.build()
    after = intelligence.files["calculator.py"].content_hash
    assert before != after
    assert any(symbol.name == "multiply" for symbol in intelligence.files["calculator.py"].symbols)


def test_repository_memory_cannot_be_forged_with_recomputed_unkeyed_hash(tmp_path: Path):
    root = _repo(tmp_path)
    store = EngineeringMemoryStore(root)
    store.create("task", "Fix multiplication")
    path = store.path_for("task")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["objective"] = "forged objective"
    payload = dict(data)
    for key in ("integrity_hmac_sha256", "integrity_key_id", "integrity_scheme"):
        payload.pop(key, None)
    data["integrity_hmac_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(MemoryIntegrityError):
        store.load("task")


def test_engineering_brain_rejects_fabricated_typed_scope_evidence(tmp_path: Path):
    root = _repo(tmp_path)
    (root / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")
    brain = EngineeringBrain(root)
    brain.prepare("Fix calculator.py", task_id="fabricated-evidence", strict=True)
    revision = workspace_revision(root)
    fabricated = ScopeExpansionEvidence(
        evidence_type=ScopeEvidenceType.IMPORT_EDGE,
        target_path="helper.py",
        source_path="calculator.py",
        evidence_id=f"repo:{revision}:import:calculator.py->helper.py",
        source_revision=revision,
        details="model invented this edge",
    )
    decision = brain.authorize_mutation(
        ["helper.py"], expansion_evidence=[fabricated]
    )
    assert not decision.allowed
    assert "evidence" in decision.reason.lower()


def test_hmac_key_is_not_stored_under_repository_nexus_home(tmp_path: Path, monkeypatch):
    root = _repo(tmp_path)
    monkeypatch.delenv("NEXUS_STATE_HMAC_KEY", raising=False)
    monkeypatch.setenv("NEXUS_HOME", str(root / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    store = EngineeringMemoryStore(root)
    memory = store.create("external-key", "Fix calculator")
    store.save(memory)
    assert not list(root.rglob("*.key"))
    assert list((tmp_path / "user-home" / ".nexusai" / "state-keys").glob("*.key"))


def test_external_edit_between_plan_and_write_is_blocked(tmp_path: Path):
    root = _repo(tmp_path)
    brain = EngineeringBrain(root)
    brain.prepare("Fix calculator.py", task_id="optimistic-concurrency", strict=True)
    (root / "calculator.py").write_text(
        "def multiply(a, b):\n    return a * b + 1\n", encoding="utf-8"
    )
    decision = brain.authorize_mutation(["calculator.py"])
    assert not decision.allowed
    assert "concurrent modification" in decision.reason.lower()


@pytest.mark.parametrize(
    ("phrase", "kind"),
    [
        ("Fix calculator.py; avoid adding dependencies", "FORBID_NEW_DEPENDENCY"),
        ("Fix calculator.py; verify.py must remain unchanged", "FORBID_FILE_WRITE"),
        ("Refactor calculator.py but preserve existing behavior", "PRESERVE_BEHAVIOR"),
        ("Fix calculator.py and maintain backward compatibility", "REQUIRE_BACKWARD_COMPATIBILITY"),
    ],
)
def test_constraint_compiler_handles_natural_variants(phrase: str, kind: str):
    compiled = ConstraintCompiler.compile(phrase)
    assert kind in {item.kind.value for item in compiled.constraints}


def test_preserve_behavior_requires_preexisting_regression_evidence():
    constraints = [item.to_dict() for item in ConstraintCompiler.compile(
        "Refactor calculator.py but preserve existing behavior"
    ).constraints]
    base = [
        {"id": "m1", "kind": "file_mutation", "status": "verified"},
        {"id": "r1", "kind": "independent_review", "status": "verified"},
    ]
    weak = SemanticVerifier().verify(
        objective="Refactor calculator.py but preserve existing behavior",
        task_type="refactor",
        evidence=[
            *base,
            {
                "id": "v1",
                "kind": "verification_check",
                "status": "verified",
                "tool": "pytest",
                "command": "pytest -q",
                "exit_code": 0,
                "metadata": {
                    "check_type": "test",
                    "test_origin": "model_generated",
                    "independently_validated": True,
                },
            },
        ],
        changed_files=["calculator.py"],
        allowed_files=["calculator.py"],
        prohibited_patterns=[],
        constraints=constraints,
    )
    assert not weak.satisfied
    assert any(
        finding.code == "SEM-BEHAVIOR-PRESERVATION-PROOF-MISSING"
        for finding in weak.findings
    )


def test_authenticated_state_rejects_corrupt_external_key(tmp_path, monkeypatch):
    from nexus.intelligence.engineering import integrity

    repository = tmp_path / "repo"
    repository.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("NEXUS_STATE_HMAC_KEY", raising=False)
    key_dir = home / ".nexusai" / "state-keys"
    key_dir.mkdir(parents=True)
    key_name = integrity._repository_id(repository) + ".key"
    (key_dir / key_name).write_bytes(b"short")
    with pytest.raises(RuntimeError, match="invalid length"):
        integrity.StateAuthenticator.for_repository(repository)
