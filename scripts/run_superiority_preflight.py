#!/usr/bin/env python3
"""Reject an invalid superiority campaign before paid executions begin."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nexus.competitive_benchmark import CompetitiveDuelRunner
from nexus.competitive_qualification import SuperiorityThresholds


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--minimum-tasks", type=int, default=50)
    parser.add_argument("--minimum-repositories", type=int, default=10)
    parser.add_argument("--trials", type=int, default=3)
    args = parser.parse_args()
    report = CompetitiveDuelRunner(args.manifest).superiority_preflight(
        thresholds=SuperiorityThresholds(
            minimum_unique_tasks=max(1, args.minimum_tasks),
            minimum_unique_repositories=max(1, args.minimum_repositories),
            minimum_trials_per_task=max(1, args.trials),
        )
    )
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
