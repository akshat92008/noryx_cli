import json
from pathlib import Path

import pytest

from nexus.dashboard import RegressionDashboard


def test_dashboard_generation(tmp_path: Path):
    result_data = {
        "schema_version": "nexus.benchmark-result.v1",
        "manifest_id": "test-manifest-1",
        "summary": {
            "total": 2,
            "passed": 1,
            "failed": 1,
            "total_duration_ms": 1500,
            "estimated_cost_usd": 0.05,
        },
        "results": [
            {
                "task_id": "task-1",
                "category": "bug-repair",
                "status": "PASSED",
                "duration_ms": 1000,
                "agent_status": "VERIFIED",
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "estimated_cost_usd": 0.02,
            },
            {
                "task_id": "task-2",
                "category": "feature-implementation",
                "status": "FAILED",
                "duration_ms": 500,
                "agent_status": "ERROR",
                "prompt_tokens": 150,
                "completion_tokens": 0,
                "estimated_cost_usd": 0.03,
            },
        ],
    }

    input_file = tmp_path / "result.json"
    input_file.write_text(json.dumps(result_data))

    output_file = tmp_path / "dashboard.html"

    RegressionDashboard.generate(str(input_file), str(output_file))

    assert output_file.exists()
    html = output_file.read_text()

    # Check for KPI cards
    assert "50.0%" in html
    assert "2" in html
    assert "$0.0500" in html
    assert "1.5s" in html

    # Check for task rows
    assert "task-1" in html
    assert "bug-repair" in html
    assert "status-passed" in html
    assert "$0.0200" in html

    assert "task-2" in html
    assert "status-failed" in html


def test_dashboard_generation_from_shipped_manifest_schema(tmp_path: Path):
    manifest = {
        "schema_version": "nexus.benchmark.v1",
        "name": "launch-suite",
        "tasks": [
            {"id": "task-1", "category": "feature"},
            {"id": "task-2", "category": "bug-repair"},
        ],
    }
    input_file = tmp_path / "manifest.json"
    input_file.write_text(json.dumps(manifest), encoding="utf-8")
    output_file = tmp_path / "dashboard.html"

    RegressionDashboard.generate(str(input_file), str(output_file))

    html = output_file.read_text(encoding="utf-8")
    assert "launch-suite" in html
    assert "task-1" in html
    assert "task-2" in html
    assert "NOT_RUN" in html
    assert "status-not-run" in html


def test_dashboard_invalid_schema(tmp_path: Path):
    result_data = {
        "schema_version": "invalid.v1",
    }
    input_file = tmp_path / "result.json"
    input_file.write_text(json.dumps(result_data))
    output_file = tmp_path / "dashboard.html"

    with pytest.raises(ValueError, match="Unsupported schema version"):
        RegressionDashboard.generate(str(input_file), str(output_file))
