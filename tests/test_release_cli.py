"""Release-facing CLI behavior and diagnostics."""

from __future__ import annotations

import io
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from nexus.cli import _configure_output_streams, non_interactive_exit_code
from nexus.doctor import run_doctor
from nexus.nova_runtime import OllamaClient
from nexus.webapp.server import _is_allowed_web_origin, _is_sensitive_path
from scripts import run_release_gate


def test_non_interactive_failure_uses_nonzero_exit_code():
    assert non_interactive_exit_code("Nova backend error: Ollama unavailable", []) == 2
    assert (
        non_interactive_exit_code(
            "Pending edit",
            [{"type": "tool_call", "success": False}],
        )
        == 2
    )


def test_non_interactive_success_uses_zero_exit_code():
    assert (
        non_interactive_exit_code(
            "Implemented and verified.",
            [{"type": "tool_call", "success": True}],
        )
        == 0
    )


def test_doctor_accepts_hosted_backend_when_ollama_is_offline(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def offline(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", offline)
    success, report = run_doctor(str(tmp_path))
    assert success
    assert "READY" in report
    assert "Local Nova" in report
    assert "Hosted provider" in report


def test_module_entrypoint_exposes_version():
    result = subprocess.run(
        [sys.executable, "-m", "nexus", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    from nexus import __version__
    assert result.stdout.strip() == f"NexusAI {__version__}"


def test_ollama_host_without_scheme_is_normalized(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434/")
    client = OllamaClient()
    assert client.base_url == "http://127.0.0.1:11434"


def test_web_file_api_classifies_secret_paths(tmp_path):
    assert _is_sensitive_path(tmp_path / ".env")
    assert _is_sensitive_path(tmp_path / ".env.production")
    assert _is_sensitive_path(tmp_path / ".git" / "config")
    assert _is_sensitive_path(tmp_path / ".nexusai" / "evidence.jsonl")
    assert not _is_sensitive_path(tmp_path / ".github" / "workflows" / "ci.yml")
    assert not _is_sensitive_path(tmp_path / "src" / "main.py")


def test_websocket_origin_is_limited_to_loopback():
    assert _is_allowed_web_origin(None)
    assert _is_allowed_web_origin("http://localhost:3000")
    assert _is_allowed_web_origin("http://127.0.0.1:8080")
    assert not _is_allowed_web_origin("https://attacker.example")
    assert not _is_allowed_web_origin("file://localhost/tmp/index.html")


def test_release_gate_uses_uv_when_managed_venv_has_no_pip(monkeypatch):
    monkeypatch.setattr(run_release_gate.importlib.util, "find_spec", lambda _name: None)
    monkeypatch.setattr(run_release_gate.shutil, "which", lambda _name: "/tools/uv")

    command = run_release_gate.wheel_install_command(
        "/venv/python", Path("/target"), Path("/dist/nexus.whl")
    )

    assert command == [
        "/tools/uv",
        "pip",
        "install",
        "--python",
        "/venv/python",
        "--no-deps",
        "--target",
        str(Path("/target")),
        str(Path("/dist/nexus.whl")),
    ]


def test_release_gate_rejects_dependency_mirror_drift(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["httpx[socks]>=0.27", "rich>=13"]\n',
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text("rich>=13\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"missing=\['httpx'\]"):
        run_release_gate.assert_dependency_mirror(tmp_path)


def test_release_gate_accepts_dependency_mirror(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["httpx[socks]>=0.27", "rich>=13"]\n',
        encoding="utf-8",
    )
    (tmp_path / "requirements.txt").write_text(
        "rich>=13\nhttpx[socks]>=0.27\n",
        encoding="utf-8",
    )

    run_release_gate.assert_dependency_mirror(tmp_path)


def test_release_gate_prefers_pip_when_available(monkeypatch):
    monkeypatch.setattr(run_release_gate.importlib.util, "find_spec", lambda _name: object())

    command = run_release_gate.wheel_install_command(
        "/venv/python", Path("/target"), Path("/dist/nexus.whl")
    )

    assert command[:4] == ["/venv/python", "-m", "pip", "install"]


def test_cli_reconfigures_legacy_output_streams_to_utf8():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")

    _configure_output_streams((stream,))
    stream.write("✓")
    stream.flush()

    assert stream.encoding.lower().replace("-", "") == "utf8"
    assert raw.getvalue() == "✓".encode()


def test_source_revision_falls_back_to_stable_archive_hash(tmp_path, monkeypatch):
    (tmp_path / "nexus").mkdir()
    (tmp_path / "nexus" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("release source\n", encoding="utf-8")

    def no_git(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, ["git", "rev-parse", "HEAD"])

    monkeypatch.setattr(run_release_gate.subprocess, "check_output", no_git)
    first = run_release_gate.source_revision(tmp_path)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "ignored.whl").write_bytes(b"generated")
    second = run_release_gate.source_revision(tmp_path)

    assert first.startswith("archive:")
    assert first == second


def test_controlled_beta_requires_truth_integrity_reports(tmp_path):
    import json
    from datetime import datetime, timezone

    from nexus.release.qualification import (
        ChannelPolicy,
        ReleaseQualification,
        RollbackPlan,
        build_supply_chain_evidence,
        sha256_file,
    )

    wheel = tmp_path / "nexus.whl"
    wheel.write_bytes(b"wheel")
    source = tmp_path / "nexus.tar.gz"
    source.write_bytes(b"source")
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"/></testsuites>',
        encoding="utf-8",
    )
    coverage = tmp_path / "coverage.xml"
    coverage.write_text('<coverage line-rate="1"/>', encoding="utf-8")
    benchmark = tmp_path / "benchmark.json"
    benchmark.write_text(
        json.dumps({"summary": {"failed": 0, "manifest_valid_tasks": 1}}),
        encoding="utf-8",
    )
    offline = tmp_path / "offline.json"
    offline.write_text(
        json.dumps(
            {
                "summary": {
                    "failed": 0,
                    "executed_scenarios": 5,
                    "real_repository_repairs": 1,
                    "intelligence_claim": "none",
                }
            }
        ),
        encoding="utf-8",
    )
    sbom = tmp_path / "sbom.json"
    sbom.write_text(
        json.dumps(
            {
                "spdxVersion": "SPDX-2.3",
                "packages": [{"name": "nexusai-cli", "versionInfo": "3.6.0"}],
            }
        ),
        encoding="utf-8",
    )
    deploy = tmp_path / "deploy.json"
    deploy.write_text(
        json.dumps(
            {
                "supervised_production_ready": True,
                "autonomous_production_ready": False,
            }
        ),
        encoding="utf-8",
    )
    reports = {
        name: {"path": path.name, "sha256": sha256_file(path)}
        for name, path in {
            "junit": junit,
            "coverage": coverage,
            "benchmark": benchmark,
            "offline_reliability": offline,
            "sbom": sbom,
            "deploy_check": deploy,
        }.items()
    }
    binding = {
        "schema_version": "nexus.release-evidence.v1",
        "version": "3.6.0",
        "source_tree_sha256": "source",
        "source_archive_sha256": sha256_file(source),
        "artifacts": {wheel.name: sha256_file(wheel), source.name: sha256_file(source)},
        "test_command": "pytest -q",
        "runner": {"os": "linux", "python": "3.13"},
        "test_summary": {"collected": 1, "passed": 1, "failed": 0, "skipped": 0},
        "reports": reports,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    qualification = ReleaseQualification(
        version="3.6.0",
        supply_chain=build_supply_chain_evidence(
            secret_scan_passed=True, artifact_paths=(wheel, source)
        ),
        rollback_plan=RollbackPlan(safe_version="uninstalled", downgrade_tested=True),
        channel_policy=ChannelPolicy(
            name="controlled-beta",
            require_artifact_evidence=True,
            require_bound_evidence=True,
            required_report_names=tuple(reports),
        ),
        evidence_binding=binding,
        expected_source_sha256="source",
        evidence_root=str(tmp_path),
    )
    assert qualification.evaluate()["status"] == "pass"
    deploy.write_text(
        json.dumps(
            {
                "supervised_production_ready": True,
                "autonomous_production_ready": True,
            }
        ),
        encoding="utf-8",
    )
    binding["reports"]["deploy_check"]["sha256"] = sha256_file(deploy)
    failures = qualification.evaluate()["failures"]
    assert "bound_evidence_autonomous_readiness_overclaimed" in failures
