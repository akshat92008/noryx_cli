#!/usr/bin/env python3
"""Qualify Nexus against real hosted providers and deterministic project oracles."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from nexus.benchmark import BenchmarkRunner, BenchmarkSuite
from nexus.models import resolve_model
from nexus.preflight import configured_hosted_credentials, probe_model
from nexus.provenance import resolve_source_identity, sha256_file

REPO = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run real-provider Nexus benchmarks. This can incur provider charges and "
            "must be opted into explicitly."
        )
    )
    parser.add_argument(
        "--manifest",
        default=str(REPO / "benchmarks" / "long_horizon.json"),
    )
    parser.add_argument("--model", default=os.environ.get("NEXUS_MODEL", "glm-5.2"))
    parser.add_argument("--model-id", default=os.environ.get("NEXUS_MODEL_ID"))
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--required-pass-rate", type=float, default=1.0)
    parser.add_argument(
        "--artifact-root",
        default=str(REPO / "verification_evidence" / "live-provider"),
    )
    parser.add_argument("--keep-workspaces", action="store_true")
    parser.add_argument("--allow-cost", action="store_true")
    args = parser.parse_args()

    if not args.allow_cost and os.environ.get("NEXUS_RUN_LIVE_PROVIDER") != "1":
        parser.error("live-provider execution requires --allow-cost or NEXUS_RUN_LIVE_PROVIDER=1")
    if not 1 <= args.trials <= 10:
        parser.error("--trials must be between 1 and 10")
    if not 0 < args.required_pass_rate <= 1:
        parser.error("--required-pass-rate must be greater than 0 and at most 1")

    if args.model_id:
        os.environ["NEXUS_MODEL_ID"] = args.model_id
    os.environ["NEXUS_MODEL"] = args.model
    model_cfg = resolve_model(args.model)
    if not model_cfg:
        parser.error(f"unknown model: {args.model}")
    if model_cfg.get("backend") == "nova":
        parser.error("live-provider qualification requires a hosted model")
    if not configured_hosted_credentials():
        parser.error("no hosted-provider credential is configured")
    probe = probe_model(model_cfg, model_name=args.model)
    if not probe.ready:
        parser.error(probe.format())

    suite = BenchmarkSuite.load(args.manifest)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_root = Path(args.artifact_root).expanduser().resolve() / stamp
    run_root.mkdir(parents=True, exist_ok=False)

    reports = []
    task_results = []
    for trial in range(1, args.trials + 1):
        trial_root = run_root / f"trial-{trial:02d}"
        report = BenchmarkRunner(
            suite,
            artifact_root=trial_root / "tasks",
            keep_workspaces=args.keep_workspaces,
        ).run()
        payload = report.to_dict()
        _write_json(trial_root / "report.json", payload)
        reports.append(
            {
                "trial": trial,
                "report": str(trial_root / "report.json"),
                "summary": payload["summary"],
            }
        )
        task_results.extend(payload["results"])

    total = len(task_results)
    passed = sum(item.get("status") == "PASSED" for item in task_results)
    verified = sum(
        item.get("status") == "PASSED" and item.get("agent_status") == "VERIFIED"
        for item in task_results
    )
    live_calls_observed = all(int(item.get("model_calls", 0) or 0) > 0 for item in task_results)
    pass_rate = passed / total if total else 0.0
    qualified = (
        bool(total)
        and pass_rate >= args.required_pass_rate
        and verified == total
        and live_calls_observed
    )
    failure_categories: dict[str, int] = {}
    for item in task_results:
        category = str(item.get("failure_type") or item.get("failure_phase") or "none")
        failure_categories[category] = failure_categories.get(category, 0) + 1
    task_evidence = [
        {
            "task_id": item.get("task_id"),
            "status": item.get("status"),
            "agent_status": item.get("agent_status"),
            "attempts": item.get("attempts", 0),
            "retries": item.get("retries", 0),
            "cost_usd": item.get("estimated_cost_usd"),
            "changed_files": item.get("changed_files", []),
            "external_verification_passed": item.get(
                "external_verification_passed", False
            ),
            "failure_phase": item.get("failure_phase", ""),
            "failure_type": item.get("failure_type", ""),
            "human_intervention": item.get("human_intervention", False),
            "quality_score": item.get("quality_score", 0.0),
        }
        for item in task_results
    ]
    grouped_statuses: dict[str, list[str]] = {}
    for item in task_results:
        grouped_statuses.setdefault(str(item.get("task_id", "unknown")), []).append(
            str(item.get("status", ""))
        )
    run_to_run_consistent = all(len(set(statuses)) == 1 for statuses in grouped_statuses.values())

    source_identity = resolve_source_identity(REPO)
    manifest_path = Path(args.manifest).expanduser().resolve()
    summary = {
        "schema_version": "nexus.live-provider-qualification.v3",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_identity": source_identity.to_dict(),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "qualification_scope": "live-provider regression suite",
        "unseen_external_campaign": False,
        "claude_code_parity_claim": False,
        "model": args.model,
        "model_id": args.model_id,
        "suite": suite.name,
        "profile": suite.profile,
        "trials": args.trials,
        "tasks": total,
        "passed": passed,
        "verified": verified,
        "pass_rate": round(pass_rate, 4),
        "required_pass_rate": args.required_pass_rate,
        "live_calls_observed": live_calls_observed,
        "qualified": qualified,
        "total_retries": sum(int(item.get("retries", 0) or 0) for item in task_results),
        "total_attempts": sum(int(item.get("attempts", 0) or 0) for item in task_results),
        "total_cost_usd": round(
            sum(float(item.get("estimated_cost_usd") or 0.0) for item in task_results), 8
        ),
        "external_verification_passed": sum(
            bool(item.get("external_verification_passed")) for item in task_results
        ),
        "human_intervention_count": sum(
            bool(item.get("human_intervention")) for item in task_results
        ),
        "failure_categories": failure_categories,
        "run_to_run_consistent": run_to_run_consistent,
        "task_evidence": task_evidence,
        "reports": reports,
    }
    _write_json(run_root / "qualification.json", summary)
    print(json.dumps({**summary, "artifact_root": str(run_root)}))
    return 0 if qualified else 1


if __name__ == "__main__":
    raise SystemExit(main())
