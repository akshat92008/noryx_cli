"""Regression tests for deterministic public-release gates."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from nexus.benchmark import BenchmarkRunner, BenchmarkSuite
from nexus.cli import _handle_benchmark
from nexus.pipeline import PipelineStage, StageResult


def test_root_benchmark_manifest_references_a_real_fixture():
    root = Path(__file__).parents[1]
    suite = BenchmarkSuite.load(root / "benchmark-manifest.json")
    report = BenchmarkRunner(suite).run(dry_run=True).to_dict()

    assert report["summary"]["failed"] == 0
    assert report["summary"]["manifest_valid_tasks"] == len(suite.tasks)


def test_blocked_benchmark_dry_run_exits_nonzero(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "broken.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "nexus.benchmark.v2",
                "name": "broken",
                "tasks": [
                    {
                        "id": "missing",
                        "category": "bug-repair",
                        "prompt": "Fix the bug",
                        "repository": "missing-repository",
                        "verification": [["python", "verify.py"]],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["nexus", "benchmark", "--manifest", str(manifest), "--dry-run"],
    )

    with pytest.raises(SystemExit) as error:
        _handle_benchmark()
    assert error.value.code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["results"][0]["status"] == "BLOCKED"


def test_not_applicable_stage_is_not_reported_as_verified():
    stage = StageResult(stage=PipelineStage.VERIFICATION, success=True, applicable=False)

    assert stage.status == "not_applicable"
    assert stage.status != "passed"
