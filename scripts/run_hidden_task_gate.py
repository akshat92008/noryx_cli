#!/usr/bin/env python3
"""Run an external hash-pinned hidden-task pack with a real hosted provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nexus.benchmark import BenchmarkRunner, BenchmarkSuite
from nexus.hidden_benchmark import HiddenBenchmarkThresholds, evaluate_hidden_results
from nexus.preflight import configured_hosted_credentials


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--minimum-tasks", type=int, default=30)
    parser.add_argument("--required-pass-rate", type=float, default=0.60)
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--allow-cost", action="store_true")
    parser.add_argument("--allow-public-manifest", action="store_true")
    args = parser.parse_args()

    if not args.allow_cost and os.environ.get("NEXUS_RUN_LIVE_PROVIDER") != "1":
        parser.error("hidden-task execution requires explicit paid-provider authorization")
    if not configured_hosted_credentials():
        parser.error("no hosted-provider credential is configured")
    if args.trials < 3:
        parser.error("production hidden-task qualification requires at least three trials")

    manifest = Path(args.manifest).expanduser().resolve()
    if not manifest.is_file():
        parser.error(f"manifest does not exist: {manifest}")
    try:
        manifest.relative_to(ROOT)
    except ValueError:
        pass
    else:
        if not args.allow_public_manifest:
            parser.error("production hidden-task manifest must be external to the source tree")
    actual_hash = _sha256(manifest)
    if actual_hash != args.manifest_sha256.lower():
        parser.error("hidden-task manifest SHA-256 mismatch")

    suite = BenchmarkSuite.load(manifest)
    if len(suite.tasks) < args.minimum_tasks:
        parser.error(
            f"hidden-task pack contains {len(suite.tasks)} tasks; {args.minimum_tasks} required"
        )

    root = Path(args.artifact_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=False)
    all_results: list[dict] = []
    reports: list[dict] = []
    for trial in range(1, args.trials + 1):
        trial_root = root / f"trial-{trial:02d}"
        report = BenchmarkRunner(
            suite,
            artifact_root=trial_root / "tasks",
            keep_workspaces=True,
        ).run()
        payload = report.to_dict()
        report_path = trial_root / "report.json"
        _write(report_path, payload)
        all_results.extend(payload["results"])
        reports.append({"trial": trial, "path": str(report_path), "summary": payload["summary"]})

    thresholds = HiddenBenchmarkThresholds(
        minimum_unique_tasks=args.minimum_tasks,
        minimum_trials_per_task=args.trials,
        minimum_verified_pass_rate=args.required_pass_rate,
    )
    evaluation = evaluate_hidden_results(all_results, thresholds=thresholds)
    output = {
        "schema_version": "nexus.hidden-task-qualification.v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest_sha256": actual_hash,
        "manifest_path_redacted": manifest.name,
        "suite": suite.name,
        "trials": args.trials,
        "thresholds": thresholds.__dict__,
        "evaluation": evaluation.to_dict(),
        "reports": reports,
    }
    _write(root / "qualification.json", output)
    print(json.dumps(output, sort_keys=True))
    return 0 if evaluation.qualified else 1


if __name__ == "__main__":
    raise SystemExit(main())
