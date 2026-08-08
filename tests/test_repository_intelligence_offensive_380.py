from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from nexus.doctor import DoctorCheck, _capability_status
from nexus.intelligence.concurrency import ConcurrencyAnalyzer
from nexus.intelligence.repository.engine import RepositoryIntelligence
from nexus.intelligence.repository.evidence import ExpansionPolicy, FailureEvidenceExtractor
from nexus.intelligence.task_profiles import RepositoryTaskKind, TaskProfiler
from nexus.multifile.ledger import CompletionLedger
from nexus.multifile.orchestrator import MultiFileOrchestrator
from nexus.planning.engineering_plan import (
    ActionType,
    EngineeringPlan,
    Hypothesis,
    PlanStep,
)
from nexus.planning.replanner import PlanReplanner
from nexus.sandbox import CommandSpec, SandboxBackend, SandboxRunner


def _repository(tmp_path: Path) -> RepositoryIntelligence:
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "api.py").write_text(
        "def old_api(value):\n    return value + 1\n", encoding="utf-8"
    )
    (tmp_path / "src" / "service.py").write_text(
        "from src.api import old_api\n\ndef use():\n    return old_api(1)\n", encoding="utf-8"
    )
    (tmp_path / "src" / "worker.py").write_text(
        "from src.api import old_api\n\ndef work():\n    return old_api(2)\n", encoding="utf-8"
    )
    (tmp_path / "src" / "public.py").write_text("from src.api import old_api\n", encoding="utf-8")
    (tmp_path / "src" / "consumer.py").write_text(
        "from src.public import old_api\n\ndef consume():\n    return old_api(3)\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_api.py").write_text(
        "from src.api import old_api\n\ndef test_api():\n    assert old_api(1) == 2\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text("[project]\nname='sample'\n", encoding="utf-8")
    repo = RepositoryIntelligence(tmp_path, state_root=tmp_path / ".state")
    repo.build(force=True)
    return repo


def test_unisolated_host_execution_requires_explicit_capability(tmp_path, monkeypatch):
    runner = SandboxRunner(tmp_path)
    monkeypatch.setattr(runner, "backend", lambda: SandboxBackend.RESTRICTED)

    default_spec = CommandSpec.create(["python", "-c", "print('ok')"], tmp_path)
    with pytest.raises(PermissionError, match="No supported OS sandbox"):
        runner.prepare(default_spec)

    still_blocked = CommandSpec.create(
        ["python", "-c", "print('ok')"], tmp_path, require_os_isolation=False
    )
    with pytest.raises(PermissionError, match="explicit"):
        runner.prepare(still_blocked)

    trusted = CommandSpec.create(
        ["python", "-c", "print('ok')"],
        tmp_path,
        require_os_isolation=False,
        allow_unisolated_host_process=True,
    )
    prepared = runner.prepare(trusted)
    assert prepared.backend == SandboxBackend.RESTRICTED
    assert prepared.network_enforced is False


def test_failure_evidence_drives_broader_context_budget(tmp_path):
    repo = _repository(tmp_path)
    bundle = repo.context_bundle("fix failing API test", max_files=2, max_total_tokens=2000)
    evidence = FailureEvidenceExtractor.extract(
        "tests/test_api.py::test_api failed with TypeError in src/service.py; "
        "repository-wide API migration has a concurrency regression",
        repository_paths=repo.files,
    )
    budget = ExpansionPolicy.derive(bundle, evidence, risk_level="critical")
    assert {"tests/test_api.py", "src/service.py"}.issubset(set(evidence.paths))
    assert "type_contract_failure" in evidence.failure_kinds
    assert evidence.migration_terms
    assert evidence.concurrency_terms
    assert budget.max_files >= 24
    assert budget.max_graph_hops >= 5


def test_task_profiler_covers_required_hard_task_classes():
    cases = {
        "Fix an unseen multi-file regression and find the root cause": RepositoryTaskKind.HIDDEN_MULTI_FILE_BUG,
        "Perform a framework migration from v4 to v5": RepositoryTaskKind.FRAMEWORK_MIGRATION,
        "Implement a new endpoint feature": RepositoryTaskKind.FEATURE_ADDITION,
        "Refactor the architecture while preserving behavior": RepositoryTaskKind.DIFFICULT_REFACTOR,
        "A test failure passes alone but fails in suite due to hidden dependency": RepositoryTaskKind.INDIRECT_TEST_FAILURE,
        "Change the public API signature across the repository and all callers": RepositoryTaskKind.REPOSITORY_API_CHANGE,
        "Fix a race condition in shared state under concurrency": RepositoryTaskKind.STATE_CONCURRENCY_DEFECT,
    }
    for objective, expected in cases.items():
        profile = TaskProfiler.classify(objective)
        assert profile.kind == expected, (objective, profile.kind)
        assert profile.verification_layers
        assert profile.completion_obligations


def test_repository_wide_api_change_makes_callers_blocking(tmp_path):
    repo = _repository(tmp_path)
    callers = [item["path"] for item in repo.find_callers("old_api", limit=20)]
    contract = MultiFileOrchestrator.derive(
        "Rename public API old_api across the repository and update all callers",
        repository=repo,
        explicit_files=["src/api.py"],
        decisive_files=["src/api.py"],
        callers=callers,
        related_tests=["tests/test_api.py"],
        task_type="repository_wide_api_change",
        risk_level="critical",
    )
    assert "src/api.py" in contract.required_change_files
    assert {"src/service.py", "src/worker.py", "tests/test_api.py"}.issubset(
        set(contract.required_change_files)
    )
    assert contract.minimum_changed_files >= 2
    assert contract.hard_enforcement
    assert any("No statically discovered caller" in item for item in contract.invariants)

    ledger = CompletionLedger.from_contract(
        contract,
        repository=repo,
        profile=TaskProfiler.classify(contract.objective),
    )
    unresolved = ledger.assess()
    assert not unresolved.complete
    assert "src/service.py" in unresolved.unresolved_paths
    assert "src/public.py" in unresolved.unresolved_paths
    assert "src/consumer.py" in unresolved.unresolved_paths
    inspect_paths = [item.path for item in ledger.obligations.values() if item.action == "inspect"]
    change_paths = [item.path for item in ledger.obligations.values() if item.action == "change"]
    verify_paths = [item.path for item in ledger.obligations.values() if item.action == "verify"]
    ledger.record("inspect", inspect_paths)
    ledger.record("change", change_paths)
    ledger.record("verify", verify_paths)
    assert ledger.assess().complete


def test_impact_closure_follows_reexports_and_transitive_importers(tmp_path):
    repo = _repository(tmp_path)
    closure = repo.impact_closure(["src/api.py"], symbols=["old_api"], max_hops=6)
    by_path = {item["path"]: item for item in closure}
    assert "src/public.py" in by_path
    assert "src/consumer.py" in by_path
    assert by_path["src/public.py"]["depth"] == 1
    assert by_path["src/consumer.py"]["depth"] >= 1
    assert any(
        reason.startswith("reverse_import:") for reason in by_path["src/public.py"]["reasons"]
    )


def test_concurrency_analyzer_requires_state_and_lifecycle_proof(tmp_path):
    source = tmp_path / "worker.py"
    source.write_text(
        "import threading\n"
        "cache = {}\n"
        "lock = threading.Lock()\n"
        "def put(key, value):\n"
        "    if key not in cache:\n"
        "        cache[key] = value\n",
        encoding="utf-8",
    )
    findings = ConcurrencyAnalyzer.analyze(tmp_path, ["worker.py"])
    kinds = {item.kind for item in findings}
    assert "module_mutable_state" in kinds
    assert "lock_boundary" in kinds
    assert "check_then_act" in kinds
    assert all(item.required_check for item in findings)


def test_replanner_requires_new_evidence_and_structural_change():
    plan = EngineeringPlan(
        objective="Fix the indirect failure",
        root_cause_hypotheses=[Hypothesis("h1", "The parser is wrong")],
        affected_scope=["src/parser.py"],
        steps=[
            PlanStep(
                step_id="mutate",
                title="Patch parser",
                objective="Patch parser",
                action_type=ActionType.MUTATE,
                intended_targets=["src/parser.py"],
                mutation_scope=["src/parser.py"],
                completion_condition="Patched",
                verification_method="pytest tests/test_parser.py",
            )
        ],
        version=1,
    )
    replanner = PlanReplanner(max_revisions=5)
    evidence = {
        "hypothesis_contradicted": True,
        "replacement_hypothesis": "The fixture mutates shared configuration",
        "failing_stack_files": ["tests/conftest.py", "src/config.py"],
        "failing_tests": ["tests/test_parser.py::test_indirect"],
        "verification_commands": ["python -m pytest tests/test_parser.py::test_indirect"],
    }
    revised, accepted = replanner.revise_plan(plan, "same patch failed", "mutate", evidence)
    assert accepted
    assert revised.version == 2
    assert revised.root_cause_hypotheses[0].status.value == "contradicted"
    assert any(step.action_type == ActionType.ANALYZE for step in revised.steps)
    assert any(step.action_type == ActionType.VERIFY for step in revised.steps)
    assert {"tests/conftest.py", "src/config.py"}.issubset(set(revised.affected_scope))

    duplicate, accepted_again = replanner.revise_plan(
        revised, "same patch failed", "mutate", evidence
    )
    assert not accepted_again
    assert duplicate is revised


def test_doctor_status_is_capability_specific():
    pass_checks = [DoctorCheck("Workspace", "pass", "ok"), DoctorCheck("Sandbox", "pass", "ok")]
    assert _capability_status(pass_checks, "plan") == "READY_FOR_PLAN_ONLY"
    assert _capability_status(pass_checks, "autonomous") == "READY_FOR_VERIFIED_REPAIR"
    degraded = [DoctorCheck("Workspace", "pass", "ok"), DoctorCheck("Sandbox", "fail", "missing")]
    assert _capability_status(degraded, "review") == "READY_FOR_ANALYSIS_ONLY"


def _competitive_report() -> dict:
    groups = []
    for task_index in range(2):
        for trial in range(1, 3):
            results = []
            for agent, verified, cost, duration in (
                ("nexus", True, 0.4, 900),
                ("direct_baseline", task_index == 0 and trial == 1, 0.3, 600),
                ("claude_code", task_index == 0, 1.0, 1000),
            ):
                results.append(
                    {
                        "agent": agent,
                        "available": True,
                        "completed": True,
                        "verified": verified,
                        "false_success": False,
                        "unexpected_files": [],
                        "duration_ms": duration,
                        "cost_usd": cost,
                        "input_tokens": 1000 + task_index,
                        "output_tokens": 200 + trial,
                        "human_interventions": 0,
                        "provenance": {
                            "argv_sha256": hashlib.sha256(f"{agent}-argv".encode()).hexdigest(),
                            "executable": f"/{agent}",
                            "version": "1.0",
                            "product_identity": {
                                "nexus": "nexusai-cli",
                                "direct_baseline": "direct-model-baseline",
                                "claude_code": "anthropic-claude-code",
                            }[agent],
                            "model_identity": (
                                "provider/model-v1"
                                if agent in {"nexus", "direct_baseline"}
                                else "anthropic/claude-v1"
                            ),
                            "repository_sha256": hashlib.sha256(
                                f"repo-{task_index}".encode()
                            ).hexdigest(),
                            "prompt_sha256": hashlib.sha256(
                                f"prompt-{task_index}".encode()
                            ).hexdigest(),
                            "candidate_isolation": {
                                "filesystem_isolation": True,
                                "network_denied": True,
                                "network_enforced": True,
                                "original_repository_unreadable": True,
                                "oracle_unreadable": True,
                            },
                            "telemetry": {
                                "source": "evaluator_harness",
                                "candidate_writable": False,
                                "record_sha256": "f" * 64,
                            },
                        },
                    }
                )
            groups.append(
                {
                    "task_id": f"task-{task_index}",
                    "repository_id": f"repo-{task_index}",
                    "category": "hidden_multi_file_bug",
                    "trial": trial,
                    "budget": {
                        "agent_timeout_seconds": 120.0,
                        "verification_timeout_seconds": 60.0,
                    },
                    "results": results,
                }
            )
    budget_policy = {
        "trials": 2,
        "timeout_seconds": 120.0,
        "verification_timeout_seconds": 60.0,
        "declared": {
            "maximum_wall_time_seconds_per_run": 120,
            "maximum_human_interventions_per_run": 0,
            "same_repository_revision": True,
            "same_task_prompt": True,
            "same_oracle": True,
            "same_network_policy": True,
        },
    }
    environment_manifest = {
        "declared": {
            "runner_image": "sha256:" + "e" * 64,
            "operating_system": "Linux 6.x",
            "hardware_class": "x86_64-8cpu-32gb",
        },
        "runtime": {
            "platform": "Linux-test",
            "machine": "x86_64",
            "python_version": "3.11.9",
            "harness": "nexus.competitive-duel.v3",
        },
    }

    def canonical(value):
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    report = {
        "dry_run": False,
        "manifest_sha256": "a" * 64,
        "qualification": {
            "blind": True,
            "oracle_withheld": True,
            "private_unseen_tasks": True,
            "independent_evaluator": True,
            "equal_budget_policy": True,
            "task_selection_frozen_before_run": True,
            "campaign_id": "campaign-private-001",
            "dataset_revision": "private-v1",
            "evaluator_id": "independent-evaluator",
            "sealed_manifest_sha256": "a" * 64,
            "oracle_bundle_sha256": "b" * 64,
            "budget_policy": budget_policy,
            "budget_policy_sha256": canonical(budget_policy),
            "environment_manifest": environment_manifest,
            "environment_manifest_sha256": canonical(environment_manifest),
        },
        "task_results": groups,
        "summary": {"campaign": "test"},
    }
    from nexus.competitive_attestation import attach_evaluator_signature

    return attach_evaluator_signature(
        report,
        private_key=b"\x01" * 32,
        evaluator_id="independent-evaluator",
    )


def _competitive_trust(report: dict) -> tuple[dict, dict]:
    qualification = report["qualification"]
    return (
        {qualification["evaluator_id"]: qualification["evaluator_public_key"]},
        {
            "campaign_id": qualification["campaign_id"],
            "dataset_revision": qualification["dataset_revision"],
            "manifest_sha256": report["manifest_sha256"],
            "oracle_bundle_sha256": qualification["oracle_bundle_sha256"],
            "evaluator_id": qualification["evaluator_id"],
        },
    )


def _evaluate_competitive(report: dict, *, thresholds):
    from nexus.competitive_qualification import evaluate_superiority_report

    keys, campaign = _competitive_trust(report)
    return evaluate_superiority_report(
        report,
        thresholds=thresholds,
        trusted_evaluator_keys=keys,
        trusted_campaign=campaign,
    )


def test_superiority_gate_is_strict_and_evidence_based():
    from nexus.competitive_qualification import (
        SuperiorityThresholds,
    )

    thresholds = SuperiorityThresholds(
        minimum_unique_tasks=2,
        minimum_unique_repositories=2,
        minimum_trials_per_task=2,
        minimum_tasks_per_category=1,
        minimum_nexus_verified_rate=0.9,
        minimum_claude_margin=0.4,
        minimum_direct_uplift=2.0,
        maximum_cost_ratio_to_claude=0.5,
        maximum_duration_ratio_to_claude=1.0,
        required_categories=("hidden_multi_file_bug",),
    )
    passed = _evaluate_competitive(_competitive_report(), thresholds=thresholds)
    assert passed.qualified
    assert passed.metrics["claude_verified_margin"] == 0.5

    unsealed = _competitive_report()
    unsealed["qualification"]["private_unseen_tasks"] = False
    failed = _evaluate_competitive(unsealed, thresholds=thresholds)
    assert not failed.qualified
    assert "qualification_flag_missing:private_unseen_tasks" in failed.failures


def test_superiority_gate_rejects_smoke_or_missing_metrics():
    from nexus.competitive_qualification import (
        SuperiorityThresholds,
    )

    report = _competitive_report()
    report["dry_run"] = True
    for group in report["task_results"]:
        for result in group["results"]:
            result["cost_usd"] = None
    thresholds = SuperiorityThresholds(
        minimum_unique_tasks=2,
        minimum_unique_repositories=2,
        minimum_trials_per_task=2,
        minimum_tasks_per_category=1,
        minimum_nexus_verified_rate=0.9,
        minimum_claude_margin=0.4,
        minimum_direct_uplift=2.0,
        required_categories=("hidden_multi_file_bug",),
    )
    evaluation = _evaluate_competitive(report, thresholds=thresholds)
    assert not evaluation.qualified
    assert "dry_run_is_not_qualification_evidence" in evaluation.failures
    assert "cost_metrics_incomplete" in evaluation.failures


def test_superiority_cli_refuses_unqualified_report(tmp_path, monkeypatch, capsys):
    import sys

    from nexus.cli.cli_impl import main

    report = tmp_path / "report.json"
    report.write_text('{"dry_run": true, "task_results": []}', encoding="utf-8")
    trust_policy = tmp_path / "trust-policy.json"
    valid = _competitive_report()
    keys, campaign = _competitive_trust(valid)
    trust_policy.write_text(
        json.dumps({"trusted_evaluators": keys, "campaign": campaign}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "noryx",
            "benchmark",
            "superiority-gate",
            "--report",
            str(report),
            "--trust-policy",
            str(trust_policy),
        ],
    )
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["qualified"] is False
    assert payload["status"] == "INSUFFICIENT_OR_FAILED_EVIDENCE"


def test_task_profiler_promotes_from_runtime_evidence():
    initial = TaskProfiler.classify("Fix the flaky behavior")
    signals = FailureEvidenceExtractor.extract(
        "tests/test_cache.py::test_parallel timed out after a deadlock in "
        "src/cache.py and src/worker.py while updating shared state",
        repository_paths=("tests/test_cache.py", "src/cache.py", "src/worker.py"),
    )
    refined = TaskProfiler.refine(initial, signals)
    assert refined.kind == RepositoryTaskKind.STATE_CONCURRENCY_DEFECT
    assert refined.risk_level == "critical"
    assert refined.max_graph_hops >= initial.max_graph_hops
    assert "runtime concurrency evidence" in refined.signals


def test_recovery_controller_derives_context_expansion_from_raw_failure(tmp_path):
    from nexus.recovery.controller import RecoveryController
    from nexus.recovery.intelligent import RecoveryAction

    controller = RecoveryController(working_dir=str(tmp_path))
    controller.handle_failure(
        "tests/test_cache.py::test_parallel timed out: deadlock in "
        "src/cache.py while updating shared state",
        objective="Fix the flaky behavior",
        context_files=["src/worker.py"],
        repository_paths=["tests/test_cache.py", "src/cache.py", "src/worker.py"],
    )
    evidence = controller.last_evidence_context
    assert evidence["missing_context"] is True
    assert evidence["concurrency_failure"] is True
    assert evidence["task_profile"]["kind"] == "state_concurrency_defect"
    assert controller.last_intelligent_decision.action == RecoveryAction.EXPAND_CONTEXT


def test_evaluator_signature_detects_post_run_tampering():
    from nexus.competitive_qualification import (
        SuperiorityThresholds,
    )

    report = _competitive_report()
    report["task_results"][0]["results"][0]["verified"] = False
    thresholds = SuperiorityThresholds(
        minimum_unique_tasks=2,
        minimum_unique_repositories=2,
        minimum_trials_per_task=2,
        minimum_tasks_per_category=1,
        required_categories=("hidden_multi_file_bug",),
    )
    evaluation = _evaluate_competitive(report, thresholds=thresholds)
    assert not evaluation.qualified
    assert any(item.startswith("qualification_signature_invalid:") for item in evaluation.failures)


def test_release_archive_normalization_is_byte_reproducible(tmp_path):
    import io
    import tarfile
    import zipfile

    from scripts.normalize_release_artifacts import normalize_sdist, normalize_wheel

    epoch = 1_786_022_400
    tar_paths = []
    wheel_paths = []
    for index in range(2):
        sdist = tmp_path / f"artifact-{index}.tar.gz"
        with tarfile.open(sdist, "w:gz") as archive:
            info = tarfile.TarInfo("package/file.txt")
            info.mtime = epoch + index + 100
            payload = b"stable-content"
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        normalize_sdist(sdist, epoch=epoch)
        tar_paths.append(sdist)

        wheel = tmp_path / f"artifact-{index}.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            info = zipfile.ZipInfo("package/file.txt")
            info.date_time = (2026, 8, 6, 12, 0, index * 2)
            archive.writestr(info, b"stable-content")
        normalize_wheel(wheel, epoch=epoch)
        wheel_paths.append(wheel)

    assert tar_paths[0].read_bytes() == tar_paths[1].read_bytes()
    assert wheel_paths[0].read_bytes() == wheel_paths[1].read_bytes()


def test_engineering_brain_expands_live_context_and_scope_from_failure(tmp_path, monkeypatch):
    from nexus.intelligence.engineering.brain import EngineeringBrain

    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / ".noryx-state"))
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    for index in range(30):
        (tmp_path / "src" / f"module_{index:02d}.py").write_text(
            f"def value_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    hidden = tmp_path / "src" / "zz_hidden_state.py"
    hidden.write_text(
        "shared = {}\n\ndef update(key, value):\n    shared[key] = value\n",
        encoding="utf-8",
    )
    failing_test = tmp_path / "tests" / "test_parallel.py"
    failing_test.write_text(
        "from src.zz_hidden_state import update\n\ndef test_parallel():\n    update('a', 1)\n",
        encoding="utf-8",
    )

    brain = EngineeringBrain(tmp_path)
    brain.prepare("Fix the flaky behavior", task_id="runtime-expansion")
    assert brain.context_bundle is not None
    before = {item.path for item in brain.context_bundle.files}

    result = brain.expand_context_from_failure(
        "verification failed under concurrent load",
        "tests/test_parallel.py::test_parallel timed out after a deadlock in "
        "src/zz_hidden_state.py while updating shared state",
    )

    assert result["task_profile"]["kind"] == "state_concurrency_defect"
    assert {"tests/test_parallel.py", "src/zz_hidden_state.py"}.issubset(set(result["paths"]))
    assert "src/zz_hidden_state.py" in brain.context_prompt
    assert set(result["paths"]).issuperset(before)
    assert result["registered_scope_evidence"]

    # Runtime evidence is trusted only after deterministic extraction and
    # repository-index confirmation; the scope guard then consumes it.
    decision = brain.authorize_mutation(
        ["src/zz_hidden_state.py"],
        reason="Fix the reproduced concurrency failure.",
    )
    assert decision.allowed, decision.reason


def test_legacy_planner_persists_structural_canonical_replan(tmp_path, monkeypatch):
    from nexus.paths import nexus_home
    from nexus.planner import Difficulty, IntentType, PlanningEngine

    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / ".noryx"))
    # planner.py resolves PLANS_DIR at import time; redirect the module-level
    # location for this isolated persistence check.
    import nexus.planner as planner_module

    monkeypatch.setattr(planner_module, "PLANS_DIR", nexus_home() / "plans")
    planner = PlanningEngine()
    analysis = {
        "intent": IntentType.FIX,
        "difficulty": Difficulty.COMPLEX,
        "plan_type": planner_module.PlanType.PLANNED,
        "skills_needed": [],
    }
    plan = planner.create_plan("Fix an indirect test failure", analysis)
    assert plan.canonical_plan
    original = json.dumps(plan.canonical_plan, sort_keys=True)

    accepted = planner.revise_canonical_plan(
        plan,
        trigger_reason="tests/test_api.py::test_api failed in src/service.py",
        failed_step_id=0,
        evidence={
            "source_type": "plan_step_failure",
            "failing_stack_files": ["tests/test_api.py", "src/service.py"],
            "failing_tests": ["tests/test_api.py::test_api"],
            "hypothesis_contradicted": True,
            "verification_commands": ["python -m pytest tests/test_api.py::test_api"],
        },
    )
    assert accepted
    assert json.dumps(plan.canonical_plan, sort_keys=True) != original
    assert plan.canonical_plan["version"] == 2
    assert any(item.get("structural_replan") for item in plan.failure_replans)
    assert plan.canonical_execution_contract["requires_reverification"] is True

    duplicate = planner.revise_canonical_plan(
        plan,
        trigger_reason="tests/test_api.py::test_api failed in src/service.py",
        failed_step_id=0,
        evidence={
            "source_type": "plan_step_failure",
            "failing_stack_files": ["tests/test_api.py", "src/service.py"],
            "failing_tests": ["tests/test_api.py::test_api"],
            "hypothesis_contradicted": True,
            "verification_commands": ["python -m pytest tests/test_api.py::test_api"],
        },
    )
    assert duplicate is False


def test_competitive_runner_seals_top_level_budget_and_environment(tmp_path):
    from nexus.competitive_benchmark import CompetitiveDuelRunner

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / ".oracle").mkdir()
    (repo / ".oracle" / "verify.py").write_text("assert True\n", encoding="utf-8")
    manifest = {
        "trials": 3,
        "timeout_seconds": 77,
        "verification_timeout_seconds": 31,
        "qualification": {},
        "agents": {
            "nexus": {
                "argv": ["python", "-c", "print('VERIFIED')"],
                "product_identity": "nexusai-cli",
                "model_identity": "provider/model-v1",
            },
            "claude_code": {
                "argv": ["python", "-c", "print('VERIFIED')"],
                "product_identity": "anthropic-claude-code",
                "model_identity": "anthropic/claude-v1",
            },
        },
        "tasks": [
            {
                "id": "task",
                "repository": str(repo),
                "verification": [["python", ".oracle/verify.py"]],
            }
        ],
        "budget_policy": {
            "maximum_wall_time_seconds_per_run": 77,
            "maximum_human_interventions_per_run": 0,
            "same_repository_revision": True,
            "same_task_prompt": True,
            "same_oracle": True,
            "same_network_policy": True,
        },
        "environment_manifest": {
            "runner_image": "sha256:" + "f" * 64,
            "operating_system": "Linux",
            "hardware_class": "equal-runner",
        },
    }
    runner = CompetitiveDuelRunner(manifest)
    budget = runner._budget_policy()
    environment = runner._environment_manifest()
    assert budget["declared"] == manifest["budget_policy"]
    assert environment["declared"] == manifest["environment_manifest"]
    assert runner._budget_policy_sha256() == runner._canonical_sha256(budget)
    assert runner._environment_manifest_sha256() == runner._canonical_sha256(environment)


def test_superiority_gate_rejects_tampered_or_placeholder_environment():
    from nexus.competitive_qualification import (
        SuperiorityThresholds,
    )

    thresholds = SuperiorityThresholds(
        minimum_unique_tasks=2,
        minimum_unique_repositories=2,
        minimum_trials_per_task=2,
        minimum_tasks_per_category=1,
        minimum_nexus_verified_rate=0.9,
        minimum_claude_margin=0.4,
        minimum_direct_uplift=2.0,
        maximum_cost_ratio_to_claude=0.5,
        required_categories=("hidden_multi_file_bug",),
    )
    tampered = _competitive_report()
    tampered["qualification"]["budget_policy"]["declared"]["maximum_wall_time_seconds_per_run"] = (
        999
    )
    evaluation = _evaluate_competitive(tampered, thresholds=thresholds)
    assert not evaluation.qualified
    assert "qualification_budget_policy_hash_mismatch" in evaluation.failures

    placeholder = _competitive_report()
    placeholder["qualification"]["environment_manifest"]["declared"]["runner_image"] = (
        "replace-with-image"
    )
    payload = placeholder["qualification"]["environment_manifest"]
    placeholder["qualification"]["environment_manifest_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    from nexus.competitive_attestation import attach_evaluator_signature

    placeholder = attach_evaluator_signature(
        placeholder,
        private_key=b"\x01" * 32,
        evaluator_id="independent-evaluator",
    )
    evaluation = _evaluate_competitive(placeholder, thresholds=thresholds)
    assert not evaluation.qualified
    assert "qualification_environment_contains_placeholder" in evaluation.failures


def test_superiority_gate_rejects_model_mismatch_and_repository_id_inflation():
    from nexus.competitive_attestation import attach_evaluator_signature
    from nexus.competitive_qualification import (
        SuperiorityThresholds,
    )

    thresholds = SuperiorityThresholds(
        minimum_unique_tasks=2,
        minimum_unique_repositories=2,
        minimum_trials_per_task=2,
        minimum_tasks_per_category=1,
        minimum_nexus_verified_rate=0.9,
        minimum_claude_margin=0.4,
        minimum_direct_uplift=2.0,
        maximum_cost_ratio_to_claude=0.5,
        required_categories=("hidden_multi_file_bug",),
    )
    report = _competitive_report()
    for group in report["task_results"]:
        for result in group["results"]:
            if result["agent"] == "direct_baseline":
                result["provenance"]["model_identity"] = "different/model-v2"
            if group["task_id"] == "task-1":
                result["provenance"]["repository_sha256"] = hashlib.sha256(b"repo-0").hexdigest()
    report = attach_evaluator_signature(
        report,
        private_key=b"\x01" * 32,
        evaluator_id="independent-evaluator",
    )
    evaluation = _evaluate_competitive(report, thresholds=thresholds)
    assert not evaluation.qualified
    assert "direct_baseline_model_mismatch" in evaluation.failures
    assert "unique_repositories:1<2" in evaluation.failures


def test_superiority_gate_requires_complete_token_and_budget_metrics():
    from nexus.competitive_attestation import attach_evaluator_signature
    from nexus.competitive_qualification import (
        SuperiorityThresholds,
    )

    thresholds = SuperiorityThresholds(
        minimum_unique_tasks=2,
        minimum_unique_repositories=2,
        minimum_trials_per_task=2,
        minimum_tasks_per_category=1,
        minimum_nexus_verified_rate=0.9,
        minimum_claude_margin=0.4,
        minimum_direct_uplift=2.0,
        maximum_cost_ratio_to_claude=0.5,
        required_categories=("hidden_multi_file_bug",),
    )
    report = _competitive_report()
    report["task_results"][0]["budget"]["agent_timeout_seconds"] = 999.0
    report["task_results"][0]["results"][0]["input_tokens"] = None
    report = attach_evaluator_signature(
        report,
        private_key=b"\x01" * 32,
        evaluator_id="independent-evaluator",
    )
    evaluation = _evaluate_competitive(report, thresholds=thresholds)
    assert not evaluation.qualified
    assert any(item.startswith("task_budget_exceeds_seal:") for item in evaluation.failures)
    assert "token_metrics_incomplete" in evaluation.failures


def test_superiority_preflight_blocks_invalid_campaign_before_execution(tmp_path):
    from nexus.competitive_benchmark import CompetitiveDuelRunner
    from nexus.competitive_qualification import SuperiorityThresholds

    tasks = []
    for index in range(2):
        repo = tmp_path / f"repo-{index}"
        repo.mkdir()
        (repo / "value.py").write_text(f"VALUE = {index}\n", encoding="utf-8")
        (repo / ".oracle").mkdir()
        (repo / ".oracle" / "verify.py").write_text("assert True\n", encoding="utf-8")
        tasks.append(
            {
                "id": f"task-{index}",
                "category": "hidden_multi_file_bug",
                "repository_id": f"repo-{index}",
                "repository": str(repo),
                "prompt": f"Repair hidden bug {index}",
                "oracle_dir": ".oracle",
                "verification": [["python", ".oracle/verify.py"]],
            }
        )
    manifest = {
        "trials": 2,
        "timeout_seconds": 120,
        "verification_timeout_seconds": 60,
        "qualification": {
            "blind": True,
            "oracle_withheld": True,
            "private_unseen_tasks": True,
            "independent_evaluator": True,
            "equal_budget_policy": True,
            "task_selection_frozen_before_run": True,
            "campaign_id": "private-campaign-001",
            "dataset_revision": "private-dataset-v1",
            "evaluator_id": "independent-evaluator",
        },
        "agents": {
            "nexus": {
                "argv": ["nexus", "run"],
                "version_argv": ["nexus", "--version"],
                "product_identity": "nexusai-cli",
                "model_identity": "provider/model-v1",
            },
            "direct_baseline": {
                "argv": ["direct-agent"],
                "version_argv": ["direct-agent", "--version"],
                "product_identity": "direct-model-baseline",
                "model_identity": "provider/model-v1",
            },
            "claude_code": {
                "argv": ["claude", "-p", "{prompt}"],
                "version_argv": ["claude", "--version"],
                "product_identity": "anthropic-claude-code",
                "model_identity": "anthropic/claude-v1",
            },
        },
        "tasks": tasks,
        "budget_policy": {
            "maximum_wall_time_seconds_per_run": 120,
            "maximum_human_interventions_per_run": 0,
            "same_repository_revision": True,
            "same_task_prompt": True,
            "same_oracle": True,
            "same_network_policy": True,
        },
        "environment_manifest": {
            "runner_image": "sha256:" + "f" * 64,
            "operating_system": "Linux 6.x",
            "hardware_class": "equal-8cpu-32gb",
        },
    }
    thresholds = SuperiorityThresholds(
        minimum_unique_tasks=2,
        minimum_unique_repositories=2,
        minimum_trials_per_task=2,
        minimum_tasks_per_category=1,
        required_categories=("hidden_multi_file_bug",),
    )
    ready = CompetitiveDuelRunner(manifest).superiority_preflight(thresholds=thresholds)
    assert ready["ready"]
    assert ready["unique_repositories"] == 2

    manifest["agents"]["direct_baseline"]["model_identity"] = "other/model-v2"
    blocked = CompetitiveDuelRunner(manifest).superiority_preflight(thresholds=thresholds)
    assert not blocked["ready"]
    assert "direct_baseline_model_mismatch" in blocked["failures"]
