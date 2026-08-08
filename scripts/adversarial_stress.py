#!/usr/bin/env python3
"""Run deterministic adversarial security and lifecycle stress scenarios."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "command-policy": ("tests/test_security_adversarial.py",),
    "sandbox-boundaries": ("tests/test_sandbox_stress.py",),
    "process-lifecycle": (
        "tests/test_noryx_public_launch_384.py::"
        "test_managed_process_escalates_reaps_unregisters_and_cleans_profile",
        "tests/test_release_hardening_383.py::test_background_output_is_bounded_during_execution",
    ),
    "truth-integrity": ("tests/test_truth_integrity_p0.py",),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=tuple(SCENARIOS),
        help="Run only this scenario; repeat to select more than one.",
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    selected = args.scenario or list(SCENARIOS)
    environment = dict(os.environ)
    environment["NORYX_DISABLE_NETWORK"] = "1"
    environment["NORYX_STATE_HMAC_KEY"] = "adversarial-stress-qualification"
    results = []
    for name in selected:
        command = [sys.executable, "-m", "pytest", "-q", *SCENARIOS[name]]
        started = time.monotonic()
        try:
            process = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=max(30, args.timeout),
                check=False,
            )
            output = (process.stdout + "\n" + process.stderr).strip()
            passed = process.returncode == 0 and "Traceback" not in output
            detail = output[-8000:]
        except subprocess.TimeoutExpired as exc:
            passed = False
            detail = f"timeout: {exc}"
        result = {
            "scenario": name,
            "passed": passed,
            "duration_seconds": round(time.monotonic() - started, 3),
            "detail": detail,
        }
        results.append(result)
        print(
            f"[{'PASS' if passed else 'FAIL'}] {name} ({result['duration_seconds']:.2f}s)",
            flush=True,
        )

    payload = {
        "schema_version": "noryx.adversarial-stress.v1",
        "passed": all(item["passed"] for item in results),
        "scenarios": results,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        target = Path(args.output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
