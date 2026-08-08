from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from nexus.architecture_health import _check_source_layout
from nexus.benchmark import BenchmarkRunner, BenchmarkSuite
from nexus.benchmark_resources import installed_core_manifest
from nexus.proof import create_proof_receipt, verify_proof_receipt, write_proof_receipt
from nexus.release.qualification import (
    ChannelPolicy,
    ReleaseQualification,
    RollbackPlan,
    build_supply_chain_evidence,
    sha256_file,
)
from nexus.session import AgentSession


def test_agent_session_blocks_without_real_execution_and_verification():
    session = AgentSession("repair bug")
    result = session.start()
    assert result["status"] == "BLOCKED"
    assert session.status.value == "BLOCKED"


def test_packaged_core_benchmark_is_self_contained():
    with installed_core_manifest() as manifest:
        assert manifest.is_file()
        assert (manifest.parent / "fixtures" / "calculator" / "verify.py").is_file()
        report = BenchmarkRunner(BenchmarkSuite.load(manifest)).run(dry_run=True).to_dict()
    assert report["summary"]["manifest_valid_tasks"] == 1
    assert report["summary"]["failed"] == 0


def test_proof_downgrades_unsupported_verified_claim_and_detects_tampering(tmp_path):
    receipt = create_proof_receipt(
        session_id="s1",
        workspace=tmp_path,
        final_report={"status": "VERIFIED", "checks": [], "costs": {}},
        evidence_records=[],
        authorized_budget_inr=20,
    )
    assert receipt["status"] == "PARTIALLY_VERIFIED"
    path = write_proof_receipt(receipt, tmp_path / "proof.json")
    valid, _ = verify_proof_receipt(path)
    assert valid
    data = json.loads(path.read_text())
    data["status"] = "VERIFIED"
    path.write_text(json.dumps(data))
    valid, detail = verify_proof_receipt(path)
    assert not valid
    assert "failed" in detail


def test_bound_release_evidence_matches_exact_artifact(tmp_path):
    wheel = tmp_path / "nexus.whl"
    wheel.write_bytes(b"wheel")
    source = tmp_path / "nexus.tar.gz"
    source.write_bytes(b"source")
    supply = build_supply_chain_evidence(secret_scan_passed=True, artifact_paths=(wheel, source))
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<?xml version="1.0"?><testsuites><testsuite tests="3" failures="0" errors="0" skipped="0"/></testsuites>'
    )
    coverage = tmp_path / "coverage.xml"
    coverage.write_text('<coverage line-rate="1"/>')
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text(json.dumps({"summary": {"failed": 0, "manifest_valid_tasks": 1}}))
    reports = {
        "junit": {"path": junit.name, "sha256": sha256_file(junit)},
        "coverage": {"path": coverage.name, "sha256": sha256_file(coverage)},
        "benchmark": {"path": benchmark.name, "sha256": sha256_file(benchmark)},
    }
    binding = {
        "schema_version": "nexus.release-evidence.v1",
        "version": "3.4.0",
        "source_tree_sha256": "source-hash",
        "source_archive_sha256": sha256_file(source),
        "artifacts": {wheel.name: sha256_file(wheel), source.name: sha256_file(source)},
        "test_command": "pytest -q",
        "runner": {"os": "linux", "python": "3.12"},
        "test_summary": {"collected": 3, "passed": 3, "failed": 0, "skipped": 0},
        "reports": reports,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    qualification = ReleaseQualification(
        version="3.4.0",
        supply_chain=supply,
        rollback_plan=RollbackPlan(safe_version="uninstalled", downgrade_tested=True),
        channel_policy=ChannelPolicy(
            name="controlled-beta",
            require_artifact_evidence=True,
            require_bound_evidence=True,
        ),
        evidence_binding=binding,
        expected_source_sha256="source-hash",
        evidence_root=str(tmp_path),
    )
    assert qualification.evaluate()["status"] == "pass"
    binding["source_tree_sha256"] = "other"
    failed = ReleaseQualification(
        version="3.4.0",
        supply_chain=supply,
        rollback_plan=RollbackPlan(safe_version="uninstalled", downgrade_tested=True),
        channel_policy=qualification.channel_policy,
        evidence_binding=binding,
        expected_source_sha256="source-hash",
        evidence_root=str(tmp_path),
    ).evaluate()
    assert "bound_evidence_source_hash_mismatch" in failed["failures"]


def test_architecture_gate_detects_module_package_collision(tmp_path):
    package = tmp_path / "nexus"
    (package / "dupe").mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "dupe.py").write_text("VALUE=1\n")
    (package / "dupe" / "__init__.py").write_text("VALUE=2\n")
    report = _check_source_layout(tmp_path)
    assert not report.passed
    assert any("module/package collision" in failure for failure in report.failures)


def test_fix_command_translates_to_quality_bounded_workflow(monkeypatch):
    import nexus.cli.cli_impl as cli

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "nexus",
            "fix",
            "repair refresh token",
            "--budget-inr",
            "15",
            "--model",
            "auto",
            "--proof",
        ],
    )
    cli._PROOF_REQUEST = None
    cli._prepare_fix_command()
    assert sys.argv[1:3] == ["--print", "--mode"]
    assert "quality" in sys.argv
    assert "15.0" in sys.argv
    assert "[NEXUS VERIFIED REPAIR]" in sys.argv[-1]
    assert cli._PROOF_REQUEST["enabled"] is True


def test_source_tree_hash_excludes_repository_runtime_state(tmp_path):
    from nexus.release.qualification import source_tree_sha256

    (tmp_path / "nexus").mkdir()
    (tmp_path / "nexus" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    baseline = source_tree_sha256(tmp_path)

    runtime = tmp_path / ".noryx" / "task-memory"
    runtime.mkdir(parents=True)
    (runtime / "task.json").write_text('{"runtime": true}\n', encoding="utf-8")
    (tmp_path / ".nexusai").mkdir()
    (tmp_path / ".nexusai" / "state.json").write_text("{}\n", encoding="utf-8")

    assert source_tree_sha256(tmp_path) == baseline
    (tmp_path / "nexus" / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert source_tree_sha256(tmp_path) != baseline
