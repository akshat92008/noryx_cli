#!/usr/bin/env python3
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nexus.competitive_benchmark import CompetitiveDuelRunner

parser=argparse.ArgumentParser(description='Run a blind matched-repository coding-agent duel')
parser.add_argument('--manifest', required=True)
parser.add_argument('--output', required=True)
parser.add_argument('--seed', type=int, default=370)
parser.add_argument('--dry-run', action='store_true')
args=parser.parse_args()
report=CompetitiveDuelRunner(args.manifest, seed=args.seed).run(output=args.output, dry_run=args.dry_run)
print(json.dumps(report.summary, indent=2, sort_keys=True))
