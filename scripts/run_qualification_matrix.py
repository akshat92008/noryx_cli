#!/usr/bin/env python3
"""Resumable isolated test matrix for Nexus release qualification.

Examples:
    python scripts/run_qualification_matrix.py prepare --shards 12
    python scripts/run_qualification_matrix.py shard 1 --shards 12
    python scripts/run_qualification_matrix.py aggregate --shards 12

Each numbered shard has a separate HOME, Nexus state directory, JUnit report,
log, and branch-coverage database. Interrupted qualification can resume without
re-running completed shards.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "release_evidence"


def _files() -> list[Path]:
    files = sorted((ROOT / "tests").glob("test_*.py"))
    if not files:
        raise RuntimeError("no tests found")
    return files


def _shards(count: int) -> list[list[Path]]:
    files = _files()
    count = min(len(files), max(1, count))
    return [files[index::count] for index in range(count)]


def _suite_counts(path: Path) -> tuple[int, int, int, int]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    return (
        sum(int(item.attrib.get("tests", 0)) for item in suites),
        sum(int(item.attrib.get("failures", 0)) for item in suites),
        sum(int(item.attrib.get("errors", 0)) for item in suites),
        sum(int(item.attrib.get("skipped", 0)) for item in suites),
    )


def prepare(count: int) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(EVIDENCE / "test-homes", ignore_errors=True)
    shutil.rmtree(EVIDENCE / "test-logs", ignore_errors=True)
    (EVIDENCE / "test-homes").mkdir()
    (EVIDENCE / "test-logs").mkdir()
    for pattern in ("junit-shard-*.xml", "junit.xml", "coverage.xml", "test-matrix.json"):
        for path in EVIDENCE.glob(pattern):
            path.unlink(missing_ok=True)
    for path in ROOT.glob(".coverage.release-*"):
        path.unlink(missing_ok=True)
    subprocess.run([sys.executable, "-m", "coverage", "erase"], cwd=ROOT, check=True)
    manifest = {
        "shards": count,
        "test_files": [path.relative_to(ROOT).as_posix() for path in _files()],
    }
    (EVIDENCE / "test-matrix-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"prepared {count} isolated shards")


def run_shard(index: int, count: int, timeout: int) -> None:
    shards = _shards(count)
    if index < 1 or index > len(shards):
        raise SystemExit(f"shard index must be between 1 and {len(shards)}")
    shard = shards[index - 1]
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    homes = EVIDENCE / "test-homes"
    logs = EVIDENCE / "test-logs"
    homes.mkdir(exist_ok=True)
    logs.mkdir(exist_ok=True)
    home = homes / f"shard-{index:03d}"
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    junit = EVIDENCE / f"junit-shard-{index:03d}.xml"
    log = logs / f"pytest-shard-{index:03d}.log"
    coverage_file = ROOT / f".coverage.release-{index:03d}"
    junit.unlink(missing_ok=True)
    coverage_file.unlink(missing_ok=True)

    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "XDG_CACHE_HOME": str(home / ".cache"),
            "NEXUS_HOME": str(home / ".nexusai"),
            "NEXUS_STATE_HMAC_KEY": f"qualification-shard-{index:03d}",
            "NEXUS_DISABLE_NETWORK": "1",
            "NEXUS_OFFLINE": "1",
            "PYTHONHASHSEED": "0",
            "COVERAGE_FILE": str(coverage_file),
        }
    )
    command = [
        sys.executable,
        "-m",
        "coverage",
        "run",
        "--branch",
        "--source=nexus",
        "-m",
        "pytest",
        "-q",
        "--disable-warnings",
        "--junitxml",
        str(junit),
        *(str(path.relative_to(ROOT)) for path in shard),
    ]
    print(f"shard {index}/{len(shards)}: " + ", ".join(path.name for path in shard))
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            check=False,
            timeout=timeout,
            text=True,
            capture_output=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"shard {index} exceeded {timeout}s") from exc
    output = (result.stdout or "") + (result.stderr or "")
    log.write_text(output, encoding="utf-8")
    print(result.stdout, end="")
    if result.returncode:
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="")
        raise SystemExit(result.returncode)
    if not junit.is_file() or not coverage_file.is_file():
        raise RuntimeError(f"shard {index} did not emit complete evidence")
    tests, failures, errors, skipped = _suite_counts(junit)
    print(
        json.dumps(
            {
                "shard": index,
                "tests": tests,
                "passed": tests - failures - errors - skipped,
                "failed": failures + errors,
                "skipped": skipped,
            },
            sort_keys=True,
        )
    )


def aggregate(count: int) -> None:
    expected_junit = [EVIDENCE / f"junit-shard-{index:03d}.xml" for index in range(1, count + 1)]
    expected_coverage = [ROOT / f".coverage.release-{index:03d}" for index in range(1, count + 1)]
    missing_junit = [str(path) for path in expected_junit if not path.is_file()]
    if missing_junit:
        raise RuntimeError("qualification matrix is missing JUnit evidence: " + ", ".join(missing_junit))

    env = dict(os.environ)
    combined_coverage = ROOT / ".coverage"
    available_coverage = [path for path in expected_coverage if path.is_file()]
    if combined_coverage.is_file():
        if available_coverage:
            subprocess.run(
                [sys.executable, "-m", "coverage", "combine", "--append", "--keep"],
                cwd=ROOT,
                env=env,
                check=True,
            )
    else:
        missing_coverage = [str(path) for path in expected_coverage if not path.is_file()]
        if missing_coverage:
            raise RuntimeError(
                "qualification matrix is missing coverage evidence: " + ", ".join(missing_coverage)
            )
        subprocess.run(
            [sys.executable, "-m", "coverage", "combine", "--keep"],
            cwd=ROOT,
            env=env,
            check=True,
        )
    coverage = EVIDENCE / "coverage.xml"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "xml",
            "--fail-under=60",
            "-o",
            str(coverage),
        ],
        cwd=ROOT,
        env=env,
        check=True,
    )

    aggregate_root = ET.Element("testsuites")
    totals = {"collected": 0, "failures": 0, "errors": 0, "skipped": 0}
    for part in expected_junit:
        root = ET.parse(part).getroot()
        suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
        for suite in suites:
            aggregate_root.append(suite)
        tests, failures, errors, skipped = _suite_counts(part)
        totals["collected"] += tests
        totals["failures"] += failures
        totals["errors"] += errors
        totals["skipped"] += skipped
    aggregate_root.attrib.update(
        tests=str(totals["collected"]),
        failures=str(totals["failures"]),
        errors=str(totals["errors"]),
        skipped=str(totals["skipped"]),
    )
    junit = EVIDENCE / "junit.xml"
    ET.ElementTree(aggregate_root).write(junit, encoding="utf-8", xml_declaration=True)
    failed = totals["failures"] + totals["errors"]
    summary = {
        "collected": totals["collected"],
        "passed": totals["collected"] - failed - totals["skipped"],
        "failed": failed,
        "skipped": totals["skipped"],
        "shards": count,
    }
    (EVIDENCE / "test-matrix.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    if failed:
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "aggregate"):
        item = subparsers.add_parser(command)
        item.add_argument("--shards", type=int, default=12)
    shard = subparsers.add_parser("shard")
    shard.add_argument("index", type=int)
    shard.add_argument("--shards", type=int, default=12)
    shard.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    if args.command == "prepare":
        prepare(args.shards)
    elif args.command == "shard":
        run_shard(args.index, args.shards, args.timeout)
    else:
        aggregate(args.shards)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
