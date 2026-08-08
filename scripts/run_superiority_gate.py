#!/usr/bin/env python3
"""Fail closed unless a real competitive report proves every superiority gate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from nexus.competitive_qualification import SuperiorityThresholds, evaluate_superiority_report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--minimum-tasks", type=int, default=50)
    parser.add_argument("--minimum-repositories", type=int, default=10)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    payload = json.loads(args.report.read_text(encoding="utf-8"))
    thresholds = SuperiorityThresholds(
        minimum_unique_tasks=args.minimum_tasks,
        minimum_unique_repositories=args.minimum_repositories,
        minimum_trials_per_task=args.trials,
    )
    evaluation = evaluate_superiority_report(payload, thresholds=thresholds)
    rendered = json.dumps(evaluation.to_dict(), indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if evaluation.qualified else 1


if __name__ == "__main__":
    raise SystemExit(main())
