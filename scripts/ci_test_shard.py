"""Run one deterministic shard of the Noryx regression suite.

Resource-intensive endurance/stress tests are deliberately excluded here and
run in their own CI job.  This keeps process-heavy qualification workloads from
sharing one long-lived pytest process with ordinary regression tests while
still ensuring every test file is exercised by the launch gate.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

RESOURCE_INTENSIVE = {
    "tests/security_adversarial/test_phase3_ledger_stress.py",
    "tests/security_adversarial/test_phase3_sandbox_stress.py",
    "tests/security_adversarial/test_phase7_repeated_sessions.py",
    "tests/security_adversarial/test_phase7_subprocess_endurance.py",
    "tests/security_adversarial/test_phase7_tool_endurance.py",
    "tests/test_sandbox_stress.py",
    "tests/test_stress_concurrent.py",
}


def regression_files(root: Path) -> list[str]:
    files: list[str] = []
    for path in sorted((root / "tests").rglob("test_*.py")):
        relative = path.relative_to(root).as_posix()
        if relative not in RESOURCE_INTENSIVE:
            files.append(relative)
    return files


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()

    if args.count <= 0 or not 0 <= args.index < args.count:
        parser.error("require count > 0 and 0 <= index < count")

    root = Path(__file__).resolve().parents[1]
    all_files = regression_files(root)
    selected = all_files[args.index :: args.count]
    if not selected:
        raise SystemExit(f"empty test shard {args.index}/{args.count}")

    print(
        f"Running deterministic regression shard {args.index + 1}/{args.count}: "
        f"{len(selected)} files",
        flush=True,
    )
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly", *selected],
        cwd=root,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
