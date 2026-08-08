#!/usr/bin/env python3
"""Run deterministic concurrent stress tests without provider credentials."""

from __future__ import annotations

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
IGNORED = (
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "*.egg-info",
    "verification_evidence",
    "runs",
)
SECRET_PREFIXES = (
    "NVIDIA_API_KEY",
    "NVIDIA_FALLBACK_API_KEY",
    "GROQ_API_KEY",
    "GROQ_FALLBACK_API_KEY",
    "OPENROUTER_API_KEY",
)


@dataclass(frozen=True)
class StressResult:
    command: tuple[str, ...]
    passed: bool
    duration_seconds: float
    detail: str = ""


def offline_env() -> dict[str, str]:
    env = dict(os.environ)
    for name in list(env):
        if name in {"NEXUS_OPENAI_API_KEY", "NEXUS_OPENAI_BASE_URL"} or name.startswith(
            SECRET_PREFIXES
        ):
            env.pop(name, None)
    env["NEXUS_DISABLE_NETWORK"] = "1"
    return env


def run_test(command: tuple[str, ...], timeout: int) -> StressResult:
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="nexus-stress-") as temporary:
            checkout = Path(temporary) / "src"
            shutil.copytree(REPO, checkout, ignore=shutil.ignore_patterns(*IGNORED))
            process = subprocess.run(
                command,
                cwd=checkout,
                env=offline_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            detail = "" if process.returncode == 0 else (process.stderr or process.stdout)[-4000:]
            return StressResult(
                command,
                process.returncode == 0,
                time.monotonic() - started,
                detail,
            )
    except subprocess.TimeoutExpired as exc:
        return StressResult(command, False, time.monotonic() - started, f"timeout: {exc}")
    except Exception as exc:  # pragma: no cover - infrastructure failure
        return StressResult(command, False, time.monotonic() - started, str(exc))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=20, help="Total matrix executions")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent workers")
    parser.add_argument("--timeout", type=int, default=300, help="Per-execution timeout")
    parser.add_argument(
        "--include-release-gate",
        action="store_true",
        help="Include the slower wheel build/install gate in the matrix",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.runs <= 200 or not 1 <= args.workers <= 16 or args.timeout < 30:
        raise SystemExit("runs must be 1..200, workers 1..16, and timeout at least 30")

    python = sys.executable
    commands: list[tuple[str, ...]] = [
        (python, "-m", "pytest", "tests/test_cli_responsiveness.py", "-q"),
        (python, "-m", "pytest", "tests/test_long_term_runtime.py", "-q"),
        (python, "-m", "pytest", "tests/test_agent_safety.py", "-q"),
        (python, "-m", "pytest", "tests/test_launch_reliability_contracts.py", "-q"),
        (python, "-m", "pytest", "tests/test_provider_chaos.py", "-q"),
    ]
    if args.include_release_gate:
        commands.append((python, "scripts/run_release_gate.py"))

    print(
        f"Starting offline Nexus stress matrix: runs={args.runs} workers={args.workers}",
        flush=True,
    )
    counts: Counter[str] = Counter()
    durations: list[float] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(run_test, commands[index % len(commands)], args.timeout)
            for index in range(args.runs)
        ]
        for completed, future in enumerate(concurrent.futures.as_completed(futures), start=1):
            result = future.result()
            counts["passed" if result.passed else "failed"] += 1
            durations.append(result.duration_seconds)
            label = "PASS" if result.passed else "FAIL"
            print(
                f"[{completed}/{args.runs}] {label} {result.duration_seconds:.2f}s "
                + " ".join(result.command[1:]),
                flush=True,
            )
            if result.detail:
                print(result.detail[-1000:], flush=True)

    print(
        "Stress summary: "
        f"passed={counts['passed']} failed={counts['failed']} "
        f"max_seconds={max(durations, default=0.0):.2f}",
        flush=True,
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
