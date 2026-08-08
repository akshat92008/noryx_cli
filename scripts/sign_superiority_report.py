#!/usr/bin/env python3
"""Seal a competitive report in an independent evaluator environment."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from nexus.competitive_attestation import sign_report_file


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Sign a completed superiority report. Run this only in the "
            "independent evaluator environment, not in the product's own CI."
        )
    )
    parser.add_argument("--report", required=True)
    parser.add_argument("--private-key", required=True)
    parser.add_argument("--evaluator-id", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    target = sign_report_file(
        args.report,
        args.private_key,
        evaluator_id=args.evaluator_id,
        output_path=args.output or None,
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
