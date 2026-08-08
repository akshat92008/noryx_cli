from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from nexus.offline_reliability_benchmark import run_offline_reliability_benchmark


def test_offline_reliability_executes_real_repair_and_adversarial_gates(tmp_path: Path):
    report = run_offline_reliability_benchmark(artifact_root=tmp_path / "artifacts")
    payload = report.to_dict()
    assert payload["summary"]["executed_scenarios"] == 5
    assert payload["summary"]["real_repository_repairs"] == 1
    assert payload["summary"]["failed"] == 0
    repair = next(item for item in payload["scenarios"] if item["category"] == "repository-repair")
    assert repair["evidence"]["run_status"] == "VERIFIED"
    assert repair["evidence"]["external_test_exit_code"] == 0
    assert repair["evidence"]["changed_files"] == ["calculator.py"]


def test_offline_reliability_cli_emits_machine_readable_report(tmp_path: Path):
    output = tmp_path / "offline.json"
    project_root = Path(__file__).resolve().parents[1]
    inherited_pythonpath = os.environ.get("PYTHONPATH", "")
    env = {
        **os.environ,
        "NEXUS_DISABLE_NETWORK": "1",
        "NEXUS_OFFLINE": "1",
        "NEXUS_HOME": str(tmp_path / "state"),
        "HOME": str(tmp_path / "home"),
        "PYTHONPATH": os.pathsep.join(
            item for item in (str(project_root), inherited_pythonpath) if item
        ),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nexus",
            "benchmark",
            "offline-reliability",
            "--output",
            str(output),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "nexus.offline-reliability.v1"
    assert payload["summary"]["failed"] == 0
    assert payload["summary"]["intelligence_claim"] == "none"
