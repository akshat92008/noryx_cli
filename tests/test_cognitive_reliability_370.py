from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from nexus.competitive_benchmark import CompetitiveDuelRunner
from nexus.intelligence.deliberation import DeliberationCompiler
from nexus.intelligence.repository.adaptive import AdaptiveContextSelector
from nexus.intelligence.repository.model import ContextCandidate, RepositoryFile, RepositorySymbol, RiskLevel
from nexus.multifile.orchestrator import MultiFileOrchestrator
from nexus.recovery.intelligent import RecoveryAction, RecoveryStateMachine
from nexus.runtime.process_state import ProcessStateRegistry, reset_process_state


def test_deliberation_compiles_falsifiable_concurrency_contract():
    contract = DeliberationCompiler.compile(
        "Fix the authentication race in src/session.py without changing the public API",
        task_type="bug_repair", risk_level="high", context_tree_hash="abc",
        decisive_files=["src/session.py"], related_tests=["tests/test_session.py"], symbols=["commit_session"],
    )
    assert contract.hypotheses
    assert any("atomic" in item.statement.lower() for item in contract.hypotheses)
    assert any("public api" in item.lower() for item in contract.invariants)
    assert contract.confidence_floor_for_completion == 0.90
    assert "model assertion is not evidence" in contract.to_prompt().lower()


def test_adaptive_context_propagates_callers_tests_and_dependencies(tmp_path):
    files = {
        "src/session.py": RepositoryFile(
            "src/session.py", size_bytes=100, imports=["src.store"],
            symbols=[RepositorySymbol(name="commit_session", kind="function", file_path="src/session.py", line=1)],
            risk_level=RiskLevel.HIGH,
        ),
        "src/store.py": RepositoryFile("src/store.py", size_bytes=80),
        "src/api.py": RepositoryFile("src/api.py", size_bytes=80, imports=["src.session"], references=["commit_session"]),
        "tests/test_session.py": RepositoryFile("tests/test_session.py", size_bytes=80, imports=["src.session"], references=["commit_session"], test_file=True),
        "pyproject.toml": RepositoryFile("pyproject.toml", size_bytes=80, config_file=True),
    }
    seed = ContextCandidate("src/session.py", "exact", "seed", 1.0, 25, RiskLevel.HIGH, score=100)
    selection = AdaptiveContextSelector(tmp_path, files).select("fix session race", [seed], max_candidates=8)
    paths = {item.path for item in selection.candidates}
    assert {"src/session.py", "src/store.py", "src/api.py", "tests/test_session.py"}.issubset(paths)
    assert "tests/test_session.py" in selection.coverage.related_tests
    assert selection.coverage.confidence >= 0.70


def test_multifile_completion_contract_blocks_partial_work(tmp_path):
    class Repo:
        tree_hash = "tree"
        files = {
            "src/a.py": RepositoryFile("src/a.py"),
            "src/b.py": RepositoryFile("src/b.py"),
            "tests/test_a.py": RepositoryFile("tests/test_a.py", test_file=True),
        }
    contract = MultiFileOrchestrator.derive(
        "Fix src/a.py and src/b.py and add regression tests",
        repository=Repo(), decisive_files=["src/a.py", "src/b.py"], callers=["src/b.py"],
        related_tests=["tests/test_a.py"], explicit_files=["src/a.py", "src/b.py"],
        task_type="bug_repair", risk_level="high",
    )
    partial = contract.assess(inspected_files=["src/a.py"], changed_files=["src/a.py"], verified_files=[])
    assert not partial.complete
    assert "src/b.py" in partial.missing_inspection
    assert "src/b.py" in partial.missing_changes
    assert "tests/test_a.py" in partial.missing_verification
    complete = contract.assess(
        inspected_files=["src/a.py", "src/b.py"],
        changed_files=["src/a.py", "src/b.py"],
        verified_files=["tests/test_a.py"],
    )
    assert complete.complete


def test_recovery_requires_evidence_delta_and_stops_loop():
    machine = RecoveryStateMachine(max_attempts=10, max_stagnant_repeats=4)
    record = {"kind": "targeted_test_failed", "summary": "test failed", "file_paths": ["a.py"]}
    context = {"repository_state": "r1", "plan_version": 1, "context_files": ["a.py"]}
    assert machine.decide(record, context).action == RecoveryAction.RETRY_SMALLER_PATCH
    assert machine.decide(record, context).action == RecoveryAction.EXPAND_CONTEXT
    assert machine.decide(record, context).action == RecoveryAction.REVISE_PLAN
    assert machine.decide(record, context).action == RecoveryAction.SWITCH_MODEL
    assert machine.decide(record, context).action == RecoveryAction.ROLLBACK
    assert machine.decide(record, context).terminal


def test_recovery_evidence_delta_resets_stagnation():
    machine = RecoveryStateMachine()
    record = {"kind": "command_failed", "summary": "same"}
    first = machine.decide(record, {"repository_state": "a", "plan_version": 1})
    repeated = machine.decide(record, {"repository_state": "a", "plan_version": 1})
    changed = machine.decide(record, {"repository_state": "b", "plan_version": 2})
    assert first.evidence_changed
    assert not repeated.evidence_changed
    assert changed.evidence_changed and changed.repeat_count == 0


def test_process_registry_terminates_registered_child():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
    ProcessStateRegistry.register_process(process)
    reset_process_state(strict=True)
    assert process.poll() is not None


def test_process_state_reset_is_idempotent():
    reset_process_state(strict=True)
    reset_process_state(strict=True)


def _make_duel_fixture(tmp_path: Path, *, broken_agent: str | None = None) -> Path:
    repo = tmp_path / "repo"
    (repo / ".oracle").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "src" / "value.py").write_text("VALUE = 0\n")
    (repo / ".oracle" / "verify.py").write_text(
        "from pathlib import Path\nassert 'VALUE = 1' in Path('src/value.py').read_text()\n"
    )
    scripts = {}
    for name in ("nexus", "claude"):
        path = tmp_path / f"{name}.py"
        if name == broken_agent:
            body = "print('SUCCESS')\n"
        else:
            body = "from pathlib import Path\nPath('src/value.py').write_text('VALUE = 1\\n')\nprint('SUCCESS')\n"
        path.write_text(body)
        scripts[name] = path
    manifest = {
        "trials": 1,
        "agents": {
            "nexus": {"argv": [sys.executable, str(scripts["nexus"])], "version_argv": [sys.executable, "--version"]},
            "claude": {"argv": [sys.executable, str(scripts["claude"])], "version_argv": [sys.executable, "--version"]},
        },
        "tasks": [{
            "id": "hidden", "repository": str(repo), "prompt": "Fix the hidden defect",
            "oracle_dir": ".oracle", "allowed_paths": ["src/**"],
            "verification": [[sys.executable, ".oracle/verify.py"]],
        }],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    return path


def test_blind_duel_uses_hidden_oracle_and_matched_scoring(tmp_path):
    report = CompetitiveDuelRunner(_make_duel_fixture(tmp_path), seed=7).run()
    assert report.summary["valid_pairs"] == 1
    assert all(item["verified"] for item in report.task_results[0]["results"])
    assert not (tmp_path / "repo" / "src" / "value.py").read_text().endswith("1\n")
    assert report.summary["parity_claim_supported"] is False


def test_blind_duel_detects_false_success(tmp_path):
    report = CompetitiveDuelRunner(_make_duel_fixture(tmp_path, broken_agent="nexus"), seed=7).run()
    nexus = next(item for item in report.task_results[0]["results"] if item["agent"] == "nexus")
    assert nexus["claimed_success"]
    assert not nexus["verified"]
    assert nexus["false_success"]


def test_duel_dry_run_reports_availability_without_execution(tmp_path):
    report = CompetitiveDuelRunner(_make_duel_fixture(tmp_path), seed=1).run(dry_run=True)
    assert all(item["available"] for item in report.task_results[0]["results"])
    assert all(not item["completed"] for item in report.task_results[0]["results"])


def test_restricted_sandbox_never_claims_native_autonomy(tmp_path, monkeypatch):
    from nexus.platform.sandbox_qualification import NativeSandboxQualifier, SandboxProbeResult
    from nexus.sandbox import SandboxBackend

    qualifier = NativeSandboxQualifier.__new__(NativeSandboxQualifier)
    qualifier.workspace = tmp_path

    class Runner:
        @staticmethod
        def backend():
            return SandboxBackend.RESTRICTED

    qualifier.runner = Runner()

    def probe(name: str):
        return SandboxProbeResult(
            name=name,
            passed=True,
            expected="passed",
            observed="passed",
            backend=SandboxBackend.RESTRICTED.value,
            evidence={},
        )

    monkeypatch.setattr(qualifier, "_workspace_write", lambda backend: probe("workspace_write_allowed"))
    monkeypatch.setattr(qualifier, "_timeout", lambda backend: probe("timeout_terminates_process_group"))
    monkeypatch.setattr(qualifier, "_outside_read", lambda backend: probe("outside_read_denied"))
    monkeypatch.setattr(qualifier, "_outside_write", lambda backend: probe("outside_write_denied"))
    monkeypatch.setattr(qualifier, "_network_denied", lambda backend: probe("network_denied"))

    result = qualifier.qualify()
    assert result.process_containment
    assert not result.filesystem_isolation
    assert not result.autonomous_ready
    assert result.supported_mode == "analysis-only"


def test_strong_sandbox_requires_all_behavioral_probes(tmp_path, monkeypatch):
    from nexus.platform.sandbox_qualification import NativeSandboxQualifier, SandboxProbeResult
    from nexus.sandbox import SandboxBackend

    qualifier = NativeSandboxQualifier.__new__(NativeSandboxQualifier)
    qualifier.workspace = tmp_path

    class Runner:
        @staticmethod
        def backend():
            return SandboxBackend.BUBBLEWRAP

    qualifier.runner = Runner()

    def probe(name: str, passed: bool = True):
        return SandboxProbeResult(
            name=name,
            passed=passed,
            expected="passed",
            observed="passed" if passed else "failed",
            backend=SandboxBackend.BUBBLEWRAP.value,
            evidence={},
        )

    monkeypatch.setattr(qualifier, "_workspace_write", lambda backend: probe("workspace_write_allowed"))
    monkeypatch.setattr(qualifier, "_timeout", lambda backend: probe("timeout_terminates_process_group"))
    monkeypatch.setattr(qualifier, "_outside_read", lambda backend: probe("outside_read_denied"))
    monkeypatch.setattr(qualifier, "_outside_write", lambda backend: probe("outside_write_denied"))
    monkeypatch.setattr(qualifier, "_network_denied", lambda backend: probe("network_denied"))
    assert qualifier.qualify().autonomous_ready

    monkeypatch.setattr(qualifier, "_network_denied", lambda backend: probe("network_denied", False))
    degraded = qualifier.qualify()
    assert degraded.filesystem_isolation
    assert not degraded.network_isolation
    assert not degraded.autonomous_ready
    assert degraded.supported_mode == "analysis-only"


def test_high_risk_prior_only_model_requires_approval(monkeypatch):
    from nexus.model_doctor import CapabilityBand, CapabilityProfile, CapabilityScore
    from nexus.model_router import EngineeringPhase, ModelRouter
    from nexus.models import ModelDescriptor, ModelTier, PrivacyClass
    import nexus.model_router as router_module

    descriptor = ModelDescriptor(
        model_id="test/prior-only",
        provider_id="test",
        display_name="Prior Only",
        model_family="test",
        local=False,
        tier=ModelTier.STRONG,
        privacy_class=PrivacyClass.APPROVED_CLOUD,
    )
    router = ModelRouter()
    requirements = router.derive_task_requirements(
        "security repair",
        phase=EngineeringPhase.PLANNING,
        risk_level="high",
    )
    scores = {
        dimension.value: CapabilityScore(score=0.95, confidence=0.95, notes=())
        for dimension in requirements.minimum_capabilities
    }
    profile = CapabilityProfile(
        model_id=descriptor.model_id,
        capabilities=scores,
        overall_band=CapabilityBand.STRONG,
        source="conservative-prior",
    )

    monkeypatch.setattr(router_module.model_registry, "list_all", lambda: [descriptor])
    monkeypatch.setattr(router_module.model_registry, "resolve_key", lambda value: value)
    monkeypatch.setattr(router_module.model_doctor, "get_profile", lambda value: profile)

    decision = router.route(requirements, ask_before_frontier=False)
    assert not decision.meets_requirements
    assert decision.approval_required
    assert decision.capability_confidence < 0.5
    assert "prior-only" in " ".join(decision.capability_gaps)
    assert decision.evaluation_source == "model_doctor:conservative-prior"
