#!/usr/bin/env python3
"""Run each pytest module in a fresh process and emit a machine-readable report.

This gate catches functional failures while preventing leaked threads, monkeypatches,
or global registries in one test module from contaminating later modules.  It is an
additional release gate; it does not replace the normal monolithic pytest run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_RESULT_RE = re.compile(r"(?P<count>\d+) (?P<kind>passed|failed|skipped|xfailed|xpassed)")


_EXCLUDED_TREE_PARTS = frozenset(
    {
        ".git",
        ".nexus",
        ".nexusai",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "release_evidence",
        ".venv",
        "venv",
        "node_modules",
    }
)


def _source_tree_sha256(root: Path) -> str:
    """Hash the qualified source tree while excluding generated runtime state."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in _EXCLUDED_TREE_PARTS or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.is_dir():
            continue
        if path.name.startswith(".coverage") or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            payload = ("symlink:" + os.readlink(path)).encode(
                "utf-8", errors="surrogateescape"
            )
        else:
            payload = path.read_bytes()
        digest.update(relative.as_posix().encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(payload).digest())
        digest.update(b"\0")
    return digest.hexdigest()


def _counts(output: str) -> dict[str, int]:
    totals: dict[str, int] = {}
    for match in _RESULT_RE.finditer(output):
        totals[match.group("kind")] = int(match.group("count"))
    return totals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", help="Test modules; defaults to tests/test_*.py")
    parser.add_argument("--timeout", type=float, default=180.0, help="Per-module timeout")
    parser.add_argument("--output", default="isolated-pytest-report.json")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    paths = [Path(item) for item in args.paths] or sorted((root / "tests").glob("test_*.py"))
    paths = [path if path.is_absolute() else root / path for path in paths]
    started = time.time()
    source_tree_before = _source_tree_sha256(root)
    results: list[dict[str, object]] = []

    for index, path in enumerate(paths, 1):
        relative = path.relative_to(root).as_posix()
        before = time.time()
        try:
            completed = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", relative],
                cwd=root,
                env=dict(os.environ),
                text=True,
                capture_output=True,
                timeout=max(1.0, args.timeout),
                check=False,
            )
            output = (completed.stdout or "") + (completed.stderr or "")
            status = "passed" if completed.returncode == 0 else "failed"
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            output = stdout + stderr
            status = "timeout"
            returncode = 124
        duration = time.time() - before
        result = {
            "path": relative,
            "status": status,
            "returncode": returncode,
            "duration_seconds": round(duration, 3),
            "counts": _counts(output),
            "tail": "\n".join(output.splitlines()[-20:]),
        }
        results.append(result)
        print(
            f"[{index:03d}/{len(paths):03d}] "
            f"{status.upper():7s} {duration:7.2f}s {relative}",
            flush=True,
        )

    totals: dict[str, int] = {}
    for result in results:
        for kind, count in result["counts"].items():
            totals[kind] = totals.get(kind, 0) + int(count)
    source_tree_after = _source_tree_sha256(root)
    source_tree_stable = source_tree_before == source_tree_after
    report = {
        "schema_version": 2,
        "command": "process-isolated pytest module gate",
        "python": sys.version,
        "duration_seconds": round(time.time() - started, 3),
        "modules": len(results),
        "module_failures": sum(1 for item in results if item["status"] != "passed"),
        "source_tree_sha256": source_tree_after,
        "source_tree_sha256_before": source_tree_before,
        "source_tree_stable": source_tree_stable,
        "test_totals": totals,
        "results": results,
    }
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = root / output_path
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    summary_keys = (
        "modules",
        "module_failures",
        "source_tree_sha256",
        "source_tree_stable",
        "test_totals",
        "duration_seconds",
    )
    print(json.dumps({key: report[key] for key in summary_keys}, indent=2))
    print(f"Report: {output_path}")
    return 1 if report["module_failures"] or not source_tree_stable else 0


if __name__ == "__main__":
    raise SystemExit(main())
