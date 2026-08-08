#!/usr/bin/env python3
"""Run eight real Nexus CLI scenarios and preserve complete raw transcripts.

This harness never substitutes model output or test results.  Every scenario
invokes ``run.py`` as a child process, records stdout/stderr/return codes, then
runs independent native verifiers against the resulting workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[1]


@dataclass
class Step:
    label: str
    command: list[str]
    expected_returncode: int = 0
    expected_stdout: str | None = None
    timeout: int = 300


@dataclass
class Scenario:
    name: str
    prompt: str
    fixtures: dict[str, str] = field(default_factory=dict)
    setup: Callable[[Path], None] | None = None
    steps: list[Step] = field(default_factory=list)
    model_call: bool = True
    model: str = "nova_codex"


def nexus_command(
    workspace: Path,
    prompt: str,
    model: str = "nova_codex",
    model_id: str | None = None,
) -> list[str]:
    command = [
        sys.executable,
        str(REPO / "run.py"),
        "--model",
        model,
        "--working-dir",
        str(workspace),
        "--permission-mode",
        "acceptEdits",
        "--mode",
        "autonomous",
        "--no-workspace",
        "--max-turns",
        "8",
        "--output-format",
        "json",
        prompt,
    ]
    if model_id:
        command[4:4] = ["--model-id", model_id]
    return command


def run_step(step: Step, cwd: Path, env: dict[str, str]) -> dict:
    started = datetime.now(timezone.utc)
    try:
        result = subprocess.run(
            step.command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=step.timeout,
        )
        returncode = result.returncode
        stdout = result.stdout
        stderr = result.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        returncode = None
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        timed_out = True
    except OSError as exc:
        returncode = None
        stdout = ""
        stderr = f"{type(exc).__name__}: {exc}\n"
        timed_out = False
    finished = datetime.now(timezone.utc)
    return {
        "label": step.label,
        "command": step.command,
        "expected_returncode": step.expected_returncode,
        "expected_stdout": step.expected_stdout,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "passed": (
            not timed_out
            and returncode == step.expected_returncode
            and (step.expected_stdout is None or stdout == step.expected_stdout)
        ),
    }


def write_transcript(path: Path, scenario: Scenario, records: list[dict], workspace: Path) -> None:
    sections = [
        f"SCENARIO: {scenario.name}",
        f"WORKSPACE: {workspace}",
        f"PROMPT: {scenario.prompt}",
        "",
    ]
    for record in records:
        sections.extend(
            [
                "=" * 88,
                f"STEP: {record['label']}",
                "COMMAND_JSON: " + json.dumps(record["command"]),
                f"EXPECTED_RETURNCODE: {record['expected_returncode']}",
                "EXPECTED_STDOUT_JSON: " + json.dumps(record["expected_stdout"]),
                f"ACTUAL_RETURNCODE: {record['returncode']}",
                f"TIMED_OUT: {record['timed_out']}",
                f"STEP_VERDICT: {'PASS' if record['passed'] else 'FAIL'}",
                "--- STDOUT (complete) ---",
                record["stdout"],
                "--- STDERR (complete) ---",
                record["stderr"],
                "--- END STEP ---",
                "",
            ]
        )
    path.write_text("\n".join(sections), encoding="utf-8")


def scenarios() -> list[Scenario]:
    return [
        Scenario(
            name="01_python_entrypoint",
            model="nova_codex",
            prompt=(
                "Create exactly one file hello.py with a main function that prints exactly "
                "hello-nexus, and call main() under an if __name__ == '__main__' guard."
            ),
            steps=[Step("native execution", ["python3", "hello.py"], expected_stdout="hello-nexus\n")],
        ),
        Scenario(
            name="02_python_surgical_bugfix",
            model="nova_codex",
            prompt=(
                "Modify exactly counter.py. Fix inclusive_count(limit) so a nonnegative limit includes "
                "the limit itself, while a negative limit still returns an empty list. Preserve the function name."
            ),
            fixtures={
                "counter.py": (
                    "def inclusive_count(limit):\n"
                    "    if limit < 0:\n"
                    "        return []\n"
                    "    return list(range(limit))\n"
                )
            },
            steps=[
                Step(
                    "behavior assertions",
                    [
                        "python3",
                        "-c",
                        "import counter; assert counter.inclusive_count(3) == [0,1,2,3]; assert counter.inclusive_count(-1) == []",
                    ],
                )
            ],
        ),
        Scenario(
            name="03_go_executable",
            model="nova_codex",
            prompt=(
                "Create exactly main.go as a standalone Go executable. It must parse two integer command-line "
                "arguments, print their sum followed by a newline, report invalid input to stderr, and exit nonzero. "
                "Include package main and func main()."
            ),
            steps=[
                Step("go compiler", ["go", "test", "main.go"]),
                Step("go behavior", ["go", "run", "main.go", "4", "5"], expected_stdout="9\n"),
            ],
        ),
        Scenario(
            name="04_cpp_dfs",
            model="nova_codex",
            prompt=(
                "Create exactly graph_dfs.cpp as a complete C++17 executable. Build the directed graph "
                "0->{1,2}, 1->{3}; run recursive depth-first traversal from 0; print exactly '0 1 3 2' and a newline. "
                "Include all required standard headers and int main()."
            ),
            steps=[
                Step("C++ compiler", ["g++", "-std=c++17", "graph_dfs.cpp", "-o", "graph_dfs"]),
                Step("C++ behavior", ["./graph_dfs"], expected_stdout="0 1 3 2\n"),
            ],
        ),
        Scenario(
            name="05_javascript_recursive_lister",
            model="nova_codex",
            prompt=(
                "Create exactly directory_lister.js as a dependency-free Node.js CLI. Given one directory argument, "
                "recursively print only regular-file paths relative to that directory, one per line, sorted "
                "lexicographically. Exit nonzero for a missing argument."
            ),
            setup=lambda root: (
                (root / "data" / "nested").mkdir(parents=True, exist_ok=True),
                (root / "data" / "a.txt").write_text("a\n", encoding="utf-8"),
                (root / "data" / "nested" / "b.txt").write_text("b\n", encoding="utf-8"),
            ),
            steps=[
                Step("JavaScript syntax", ["node", "--check", "directory_lister.js"]),
                Step(
                    "recursive listing behavior",
                    ["node", "directory_lister.js", "data"],
                    expected_stdout="a.txt\nnested/b.txt\n",
                ),
            ],
        ),
        Scenario(
            name="06_registry_checked_package_json",
            model="nova_codex",
            prompt=(
                "Create exactly package.json as valid JSON with name 'nexus-e2e', version '1.0.0', private true, "
                "and exactly one runtime dependency: lodash at version '^4.17.21'."
            ),
            steps=[
                Step(
                    "manifest assertions",
                    [
                        "python3",
                        "-c",
                        (
                            "import json; d=json.load(open('package.json')); "
                            "assert d['name']=='nexus-e2e'; assert d['version']=='1.0.0'; "
                            "assert d['private'] is True; assert d['dependencies']=={'lodash':'^4.17.21'}"
                        ),
                    ],
                )
            ],
        ),
        Scenario(
            name="07_dangerous_command_confirmation",
            prompt="!rm -rf ./sentinel",
            setup=lambda root: (root / "sentinel").mkdir(),
            model_call=False,
            steps=[Step("sentinel remains", ["test", "-d", "sentinel"])],
        ),
        Scenario(
            name="08_slopsquatting_block",
            prompt="!python3 -m pip install nexus-definitely-not-real-9f3a7c1b",
            model_call=False,
            steps=[],
        ),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-root", default=str(REPO / "verification_evidence"))
    parser.add_argument("--workspace-root", default=tempfile.gettempdir())
    parser.add_argument("--model", default=os.environ.get("NEXUS_E2E_MODEL"), help="Override the model for all model-backed scenarios")
    parser.add_argument("--model-id", default=os.environ.get("NEXUS_MODEL_ID"), help="Provider model ID for --model custom")
    parser.add_argument("--scenario", action="append", help="Run only the named scenario (repeatable)")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact_dir = Path(args.artifact_root).resolve() / stamp
    raw_dir = artifact_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)
    evidence_dir = artifact_dir / "evidence"
    evidence_dir.mkdir()
    workspace_root = Path(args.workspace_root).resolve() / f"nexus-release-e2e-{stamp}"
    workspace_root.mkdir(parents=True, exist_ok=False)

    env = dict(os.environ)
    env.setdefault("HOME", str(Path(tempfile.gettempdir()) / "nexus-home"))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["CI"] = "true"

    summaries = []
    selected = scenarios()
    if args.scenario:
        requested = set(args.scenario)
        selected = [item for item in selected if item.name in requested]
        missing = requested - {item.name for item in selected}
        if missing:
            parser.error("unknown scenario(s): " + ", ".join(sorted(missing)))

    for scenario in selected:
        workspace = workspace_root / scenario.name
        workspace.mkdir(parents=True)
        for rel, content in scenario.fixtures.items():
            target = workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        if scenario.setup:
            scenario.setup(workspace)

        records = []
        setup_steps = [
            Step("git init", ["git", "init"]),
            Step("git identity email", ["git", "config", "user.email", "nexus-e2e@example.invalid"]),
            Step("git identity name", ["git", "config", "user.name", "Nexus E2E"]),
            Step("git add baseline", ["git", "add", "."]),
            Step("git baseline commit", ["git", "commit", "--allow-empty", "-m", "baseline"]),
        ]
        for step in setup_steps:
            records.append(run_step(step, workspace, env))

        effective_model = args.model or scenario.model
        expected_cli_code = 0 if scenario.model_call else 2
        cli_record = run_step(
                Step(
                    "Nexus CLI",
                    nexus_command(workspace, scenario.prompt, effective_model, args.model_id),
                    expected_returncode=expected_cli_code,
                    timeout=600,
                ),
                workspace,
                env,
            )
        records.append(cli_record)
        try:
            cli_payload = json.loads(cli_record["stdout"])
            session_id = cli_payload.get("session_id")
            evidence_path = Path(env["HOME"]) / ".nexusai" / "evidence" / f"{session_id}.jsonl"
            if session_id and evidence_path.is_file():
                records.append(
                    run_step(
                        Step("Nexus evidence trail", ["cat", str(evidence_path)]),
                        workspace,
                        env,
                    )
                )
                (evidence_dir / f"{scenario.name}.jsonl").write_text(
                    evidence_path.read_text(encoding="utf-8"), encoding="utf-8"
                )
        except (json.JSONDecodeError, OSError):
            pass
        for step in scenario.steps:
            records.append(run_step(step, workspace, env))

        # A real CLI shell operation proves the resulting git state is visible
        # through Nexus, while git diff --check independently checks whitespace.
        records.append(
            run_step(
                Step("Nexus git status", nexus_command(workspace, "!git status --short", effective_model, args.model_id)),
                workspace,
                env,
            )
        )
        records.append(run_step(Step("git diff check", ["git", "diff", "--check"]), workspace, env))

        passed = all(record["passed"] for record in records)
        transcript = raw_dir / f"{scenario.name}.log"
        write_transcript(transcript, scenario, records, workspace)
        summaries.append(
            {
                "name": scenario.name,
                "model": effective_model,
                "model_id": args.model_id,
                "passed": passed,
                "workspace": str(workspace),
                "transcript": str(transcript),
                "steps": [
                    {
                        "label": record["label"],
                        "returncode": record["returncode"],
                        "expected_returncode": record["expected_returncode"],
                        "passed": record["passed"],
                    }
                    for record in records
                ],
            }
        )
        print(f"{scenario.name}: {'PASS' if passed else 'FAIL'} -> {transcript}", flush=True)

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository": str(REPO),
        "workspace_root": str(workspace_root),
        "python": sys.version,
        "scenarios": summaries,
        "passed": sum(item["passed"] for item in summaries),
        "failed": sum(not item["passed"] for item in summaries),
    }
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"artifact_dir": str(artifact_dir), "passed": manifest["passed"], "failed": manifest["failed"]}))
    return 0 if manifest["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
