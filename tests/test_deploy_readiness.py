from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def _env(tmp_path: Path) -> dict[str, str]:
    root = Path(__file__).resolve().parents[1]
    inherited = os.environ.get("PYTHONPATH", "")
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(item for item in (str(root), inherited) if item),
        "HOME": str(tmp_path / "home"),
        "NEXUS_HOME": str(tmp_path / "state"),
        "NEXUS_STATE_HMAC_KEY": "deploy-readiness-test-key",
        "NVIDIA_API_KEY": "test-deploy-key",
        "NEXUS_DISABLE_NETWORK": "1",
        "NEXUS_OFFLINE": "1",
    }


def test_deploy_check_deep_refuses_production_without_native_isolation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    output = tmp_path / "deploy.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "nexus",
            "deploy",
            "check",
            "--working-dir",
            str(workspace),
            "--mode",
            "review",
            "--deep",
            "--output",
            str(output),
            "--json",
        ],
        cwd=tmp_path,
        env=_env(tmp_path),
        capture_output=True,
        text=True,
        timeout=90,
        check=False,
    )
    assert result.returncode == 2, result.stderr + result.stdout
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["authenticated_state_ready"] is True
    assert payload["offline_reliability_summary"]["failed"] == 0
    assert payload["doctor_ready"] is False
    assert "READY_FOR_ANALYSIS_ONLY" in payload["doctor_report"]
    assert payload["supervised_production_ready"] is False
    assert payload["autonomous_production_ready"] is False
    assert payload["production_claim"] is False
    assert payload["production_claim_scope"] == "none"
