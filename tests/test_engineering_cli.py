from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _run(*args: str, cwd: Path, home: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["NEXUS_HOME"] = str(home / ".nexus")
    source_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = source_root + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "nexus", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )


def test_intelligence_inspect_is_repository_aware_and_model_free(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "test_calculator.py").write_text(
        "from calculator import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    result = _run(
        "intelligence",
        "inspect",
        "Fix calculator.py and verify test_calculator.py without changing README.md",
        "--working-dir",
        str(repo),
        "--strict",
        "--json",
        cwd=repo,
        home=tmp_path / "home",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "READY_TO_PLAN"
    assert "calculator.py" in payload["decisive_files"]
    assert payload["repository_tree_hash"]
    assert payload["scope_contract"]["strict"] is True


def test_matched_benchmark_cli_enforces_identical_trial_contract(tmp_path: Path):
    direct = []
    nexus = []
    for index in range(6):
        common = {
            "task_id": f"task-{index}",
            "model": "affordable-model-v1",
            "source_revision": "abc123",
            "budget_usd": 1.0,
            "cost_usd": 0.4,
            "regressions": 0,
        }
        direct.append(common | {"status": "VERIFIED" if index < 2 else "FAILED", "verified": index < 2, "claimed_success": index < 2})
        nexus.append(common | {"status": "VERIFIED" if index < 4 else "FAILED", "verified": index < 4, "claimed_success": index < 4})
    direct_path = tmp_path / "direct.json"
    nexus_path = tmp_path / "nexus.json"
    direct_path.write_text(json.dumps({"trials": direct}), encoding="utf-8")
    nexus_path.write_text(json.dumps({"trials": nexus}), encoding="utf-8")
    result = _run(
        "benchmark",
        "compare-matched",
        "--direct",
        str(direct_path),
        "--nexus",
        str(nexus_path),
        cwd=tmp_path,
        home=tmp_path / "home",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["uplift"] == 2.0
    assert payload["matched_trials"] == 6


def test_deploy_check_fails_closed_when_host_is_not_ready(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = _run(
        "deploy",
        "check",
        "--working-dir",
        str(repo),
        "--mode",
        "autonomous",
        "--json",
        cwd=repo,
        home=tmp_path / "home",
    )
    assert result.returncode in {0, 2}
    payload = json.loads(result.stdout)
    assert payload["status"] in {"READY", "NOT_READY"}
    assert payload["architecture"]["passed"] is True
    assert payload["installed_benchmark_ready"] is True
    assert payload["production_claim"] is False
    if payload["status"] == "NOT_READY":
        assert result.returncode == 2
