"""Public-launch regressions for the Noryx 3.8.4 audit remediation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_noryx_module_and_legacy_module_report_canonical_brand() -> None:
    from nexus import __version__

    for module in ("noryx", "nexus"):
        result = subprocess.run(
            [sys.executable, "-m", module, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == f"Noryx {__version__}"

    import noryx.sandbox as canonical_sandbox

    from nexus.sandbox import SandboxBackend

    assert canonical_sandbox.SandboxBackend.BLOCKED.value == SandboxBackend.BLOCKED.value


def test_noryx_home_precedes_legacy_environment(tmp_path: Path, monkeypatch) -> None:
    from nexus.paths import nexus_home, noryx_home

    canonical = tmp_path / "canonical"
    legacy = tmp_path / "legacy"
    monkeypatch.setenv("NORYX_HOME", str(canonical))
    monkeypatch.setenv("NEXUS_HOME", str(legacy))
    assert noryx_home() == canonical.resolve()
    assert nexus_home() == canonical.resolve()


def test_noryx_network_kill_switch_precedes_legacy_value(monkeypatch) -> None:
    from nexus.network_policy import network_globally_disabled

    monkeypatch.setenv("NEXUS_DISABLE_NETWORK", "0")
    monkeypatch.setenv("NORYX_DISABLE_NETWORK", "1")
    assert network_globally_disabled() is True


def test_project_memory_creates_canonical_instruction_file(tmp_path: Path) -> None:
    from nexus.project_memory import ProjectMemory

    created = Path(ProjectMemory(str(tmp_path)).create_default_rules())
    assert created.name == "NORYX.md"
    assert "Noryx" in created.read_text(encoding="utf-8")


def test_report_cannot_authorize_its_own_evaluator_key() -> None:
    from nexus.competitive_attestation import (
        attach_evaluator_signature,
        verify_evaluator_signature,
    )

    report = {
        "manifest_sha256": "a" * 64,
        "qualification": {
            "campaign_id": "sealed-campaign",
            "dataset_revision": "private-v1",
            "evaluator_id": "evaluator",
            "oracle_bundle_sha256": "b" * 64,
            "budget_policy_sha256": "c" * 64,
            "environment_manifest_sha256": "d" * 64,
        },
        "task_results": [],
        "summary": {},
    }
    trusted = attach_evaluator_signature(report, private_key=b"\x01" * 32, evaluator_id="evaluator")
    attacker = attach_evaluator_signature(
        report, private_key=b"\x02" * 32, evaluator_id="evaluator"
    )
    valid, detail = verify_evaluator_signature(
        attacker,
        trusted_public_keys={"evaluator": trusted["qualification"]["evaluator_public_key"]},
    )
    assert valid is False
    assert "trusted key" in detail


def test_superiority_gate_requires_out_of_band_campaign_identity() -> None:
    from nexus.competitive_qualification import evaluate_superiority_report

    evaluation = evaluate_superiority_report(
        {"dry_run": False, "qualification": {}, "task_results": []},
        trusted_evaluator_keys={},
        trusted_campaign=None,
    )
    assert evaluation.qualified is False
    assert "external_campaign_trust_missing" in evaluation.failures


def test_evaluator_metrics_must_be_outside_candidate_workspace(tmp_path: Path) -> None:
    from nexus.competitive_benchmark import AgentInvocation, CompetitiveDuelRunner

    runner = CompetitiveDuelRunner.__new__(CompetitiveDuelRunner)
    runner.manifest = {"evaluator_metrics_root": str(tmp_path)}
    runner.manifest_path = None
    invocation = AgentInvocation(
        name="noryx", argv=(sys.executable,), evaluator_metrics_file="{task_id}.json"
    )
    task = {"id": "task-1"}
    workspace = tmp_path / "task-1-workspace"
    workspace.mkdir()

    # A candidate-controlled file under its workspace is never accepted.
    candidate_record = workspace / "record.json"
    candidate_record.write_text("{}", encoding="utf-8")
    candidate_invocation = AgentInvocation(
        name="noryx",
        argv=(sys.executable,),
        evaluator_metrics_file=str(candidate_record),
    )
    assert runner._load_evaluator_metrics(candidate_invocation, task, 1, workspace) == {}

    record = tmp_path / "task-1.json"
    record.write_text(
        json.dumps(
            {
                "source": "evaluator_harness",
                "task_id": "task-1",
                "agent": "noryx",
                "trial": 1,
                "cost_usd": 0.25,
                "input_tokens": 100,
                "output_tokens": 20,
                "human_interventions": 0,
            }
        ),
        encoding="utf-8",
    )
    accepted = runner._load_evaluator_metrics(invocation, task, 1, workspace)
    assert accepted["cost_usd"] == 0.25
    assert accepted["_provenance"]["candidate_writable"] is False


def test_macos_profile_uses_explicit_mach_services_and_is_cleanable(tmp_path: Path) -> None:
    from nexus.sandbox import CommandSpec, SandboxRunner

    runner = SandboxRunner(tmp_path)
    _, profile = runner._macos_command(
        CommandSpec.create([sys.executable, "-V"], tmp_path), tmp_path
    )
    content = profile.read_text(encoding="utf-8")
    try:
        assert "(allow mach-lookup)" not in content
        assert '(global-name "com.apple.system.logger")' in content
    finally:
        profile.unlink(missing_ok=True)
    assert not profile.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group semantics")
def test_managed_process_escalates_reaps_unregisters_and_cleans_profile(
    tmp_path: Path,
) -> None:
    from nexus.process_gateway import ManagedProcess
    from nexus.runtime.process_state import ProcessStateRegistry
    from nexus.sandbox import PreparedCommand, SandboxBackend

    cleanup = tmp_path / "profile.sb"
    cleanup.write_text("test", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import signal,time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "print('ready', flush=True); time.sleep(60)"
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    managed = ManagedProcess(
        process,
        PreparedCommand(
            argv=(sys.executable,),
            cwd=str(tmp_path),
            env={},
            backend=SandboxBackend.UNISOLATED_HOST,
            cleanup_path=str(cleanup),
        ),
    )
    assert managed.stdout.readline().strip() == "ready"
    managed.terminate(grace_seconds=0.05)
    assert managed.poll() is not None
    assert managed.pid not in ProcessStateRegistry._processes
    assert not cleanup.exists()


def test_authoritative_command_policy_rejects_compound_tokens(tmp_path: Path) -> None:
    from nexus.security.command_policy import CommandPolicy, CommandRisk

    policy = CommandPolicy(tmp_path)
    assert policy.classify([sys.executable, "-m", "pytest"]) == CommandRisk.VALIDATION
    assert policy.classify(["git", "status", "&&", "curl", "example.test"]) == CommandRisk.UNKNOWN
