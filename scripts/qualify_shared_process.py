#!/usr/bin/env python3
"""Run the full pytest suite in one interpreter with a hard process watchdog.

A JUnit/pass summary is not sufficient evidence if the Python interpreter never
returns control to the parent.  This qualifier therefore records the observed
process exit, kills the complete process group on timeout, and binds the result
to the exact source/dependency identity.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nexus.provenance import resolve_source_identity, source_tree_sha256


def terminate_group(process: subprocess.Popen[bytes]) -> bool:
    """Terminate the entire test process tree and report whether it exited."""
    if process.poll() is not None:
        return True
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        process.wait(timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            return process.poll() is not None
    return process.poll() is not None


def _summary(stdout: str) -> dict[str, int]:
    result = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for key in result:
        match = re.search(rf"(\d+) {key}", stdout)
        if match:
            result[key] = int(match.group(1))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--output", default="shared-process-report.json")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()

    before = source_tree_sha256(ROOT)
    identity = resolve_source_identity(ROOT)
    started = time.monotonic()
    out_file = tempfile.NamedTemporaryFile(prefix="nexus-shared-", suffix=".out", delete=False)
    err_file = tempfile.NamedTemporaryFile(prefix="nexus-shared-", suffix=".err", delete=False)
    command = [sys.executable, "-m", "pytest", "-q", "--disable-warnings", *(args.paths or ["tests"])]
    kwargs: dict[str, object] = {
        "cwd": ROOT,
        "stdout": out_file,
        "stderr": err_file,
        "env": dict(os.environ),
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)  # type: ignore[arg-type]
    timed_out = False
    forced_termination = False
    try:
        process.wait(timeout=max(1.0, args.timeout))
    except subprocess.TimeoutExpired:
        timed_out = True
        forced_termination = True
        terminate_group(process)
    finally:
        out_file.close()
        err_file.close()

    stdout_path = Path(out_file.name)
    stderr_path = Path(err_file.name)
    stdout = stdout_path.read_text(errors="replace")
    stderr = stderr_path.read_text(errors="replace")
    stdout_path.unlink(missing_ok=True)
    stderr_path.unlink(missing_ok=True)

    after = source_tree_sha256(ROOT)
    clean_exit_observed = process.poll() is not None and not timed_out and process.returncode == 0
    summary = _summary(stdout)
    report = {
        "schema_version": "nexus.shared-process.v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_identity": identity.to_dict(),
        "command": command,
        "pid": process.pid,
        "returncode": process.returncode,
        "timed_out": timed_out,
        "forced_termination": forced_termination,
        "clean_exit_observed": clean_exit_observed,
        "duration_seconds": round(time.monotonic() - started, 3),
        "source_tree_sha256_before": before,
        "source_tree_sha256_after": after,
        "source_tree_stable": before == after,
        "test_summary": summary,
        "stdout_tail": "\n".join(stdout.splitlines()[-50:]),
        "stderr_tail": "\n".join(stderr.splitlines()[-50:]),
    }
    output = Path(args.output)
    output = output if output.is_absolute() else ROOT / output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(stdout, end="")
    print(stderr, file=sys.stderr, end="")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "returncode",
                    "timed_out",
                    "clean_exit_observed",
                    "duration_seconds",
                    "source_tree_stable",
                )
            },
            indent=2,
        )
    )
    return 0 if clean_exit_observed and before == after else 1


if __name__ == "__main__":
    raise SystemExit(main())
