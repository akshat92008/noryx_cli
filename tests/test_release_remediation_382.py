"""Regression coverage for the 3.8.2 launch-remediation controls."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from nexus.agent import Agent
from nexus.doctor import doctor_report
from nexus.provenance import resolve_source_identity
from nexus.qualification_environment import qualify_environment
from nexus.sandbox import CommandSpec, SandboxRunner


def test_source_identity_is_never_empty_for_archive_tree(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='fixture'\ndependencies=[]\n")
    (tmp_path / "module.py").write_text("VALUE = 1\n")

    identity = resolve_source_identity(tmp_path)

    assert identity.revision.startswith(("archive:", "git:"))
    assert identity.source_tree_sha256


def test_environment_qualification_rejects_declared_version_drift(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='fixture'\ndependencies=['rich>=13,<15']\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "nexus.qualification_environment.importlib.metadata.version",
        lambda _name: "15.0.0",
    )
    monkeypatch.setattr(
        "nexus.qualification_environment.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="No broken requirements found.\n", stderr=""),
    )

    report = qualify_environment(tmp_path)

    assert report.passed is False
    assert report.dependencies[0].passed is False
    assert report.dependencies[0].installed_version == "15.0.0"


def test_doctor_machine_payload_is_json_serializable(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-doctor-test")
    ready, payload = doctor_report(tmp_path, mode="plan")

    encoded = json.dumps(payload)

    assert encoded
    assert payload["schema_version"] == "nexus.doctor.v1"
    assert isinstance(payload["checks"], list)
    assert payload["ready"] is ready


def test_agent_close_closes_provider_exactly_once(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-close-test")
    agent = Agent(api_key="nvapi-close-test", working_dir=str(tmp_path), workspace_isolation=False)
    provider = MagicMock()
    agent.client = provider

    first = agent.close()
    second = agent.close()

    provider.close.assert_called_once_with()
    assert first["provider_closed"] is True
    assert second["already_closed"] is True


@pytest.mark.skipif(os.name != "posix", reason="process-group inheritance regression is POSIX-specific")
def test_sandbox_does_not_hang_on_descendant_inherited_pipe(tmp_path: Path):
    """A descendant holding inherited stdio must not keep Nexus blocked forever."""
    code = (
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)']); "
        "print('parent-done')"
    )
    spec = CommandSpec.create(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        timeout_seconds=5,
        require_os_isolation=False,
        allow_unisolated_host_process=True,
    )
    started = time.monotonic()

    result = SandboxRunner(tmp_path).run(spec)

    elapsed = time.monotonic() - started
    assert elapsed < 4
    assert "parent-done" in result.stdout
    assert result.stream_cleanup_failed is True
    assert result.success is False


def test_competitive_process_capture_reaps_inherited_stdio_descendant():
    import os
    import sys
    import time

    if os.name != "posix":
        import pytest
        pytest.skip("POSIX process-group regression")

    from nexus.competitive_benchmark import _run_captured_process

    code = (
        "import subprocess,sys; "
        "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "print(p.pid, flush=True)"
    )
    started = time.monotonic()
    result = _run_captured_process([sys.executable, "-c", code], timeout=4)
    elapsed = time.monotonic() - started

    assert result.returncode == 0
    assert not result.timed_out
    assert result.descendants_reaped
    assert result.stdout.strip().isdigit()
    assert elapsed < 3.0
