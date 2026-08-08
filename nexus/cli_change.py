"""
Sprint 8 CLI Commands for Multi-File Engineering (`nexus change ...`).

Subcommands:
- nexus change analyze <symbol> [--path <path>]
- nexus change validate <change-set-json>
- nexus change execute <change-set-json> [--dry-run]
- nexus change status <run-id>
- nexus change rollback <run-id> [--stage <stage-id>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from nexus.multifile.consistency import ChangeSetConsistencyValidator
from nexus.multifile.contracts import (
    ContractChange,
    ContractScope,
    ContractType,
    EngineeringChangeSet,
    SymbolReference,
)
from nexus.multifile.impact import ImpactAnalyzer
from nexus.multifile.patch import MultiFilePatchManager
from nexus.multifile.persistence import ChangeSetPersistence
from nexus.multifile.staged_execution import StagedChangeSetExecutor


def handle_change_command(args: argparse.Namespace) -> int:
    """Entry point for `nexus change ...` subcommands."""
    subcommand = getattr(args, "change_subcommand", None)

    if subcommand == "analyze":
        return _cmd_analyze(args)
    elif subcommand == "validate":
        return _cmd_validate(args)
    elif subcommand == "execute":
        return _cmd_execute(args)
    elif subcommand == "status":
        return _cmd_status(args)
    elif subcommand == "rollback":
        return _cmd_rollback(args)
    else:
        print("Usage: nexus change {analyze|validate|execute|status|rollback} [options]")
        return 1


def add_change_subparsers(subparsers: argparse._SubParsersAction) -> None:
    """Register `nexus change` and its subcommands with argparse."""
    change_parser = subparsers.add_parser(
        "change",
        help="Multi-file engineering, refactoring, and migration commands",
    )
    change_subparsers = change_parser.add_subparsers(dest="change_subcommand")

    # analyze
    p_analyze = change_subparsers.add_parser("analyze", help="Analyze impact of a contract change")
    p_analyze.add_argument("symbol", help="Symbol or function name to analyze")
    p_analyze.add_argument("--path", "-p", default="", help="Definition file path")
    p_analyze.add_argument("--repo-root", default=".", help="Repository root directory")

    # validate
    p_validate = change_subparsers.add_parser("validate", help="Validate an EngineeringChangeSet JSON file")
    p_validate.add_argument("changeset_file", help="Path to change set JSON file")
    p_validate.add_argument("--repo-root", default=".", help="Repository root directory")

    # execute
    p_execute = change_subparsers.add_parser("execute", help="Execute an EngineeringChangeSet")
    p_execute.add_argument("changeset_file", help="Path to change set JSON file")
    p_execute.add_argument("--dry-run", action="store_true", help="Validate without mutating files")
    p_execute.add_argument("--repo-root", default=".", help="Repository root directory")

    # status
    p_status = change_subparsers.add_parser("status", help="Check status of a multi-file run")
    p_status.add_argument("run_id", help="Run ID to check")
    p_status.add_argument("--repo-root", default=".", help="Repository root directory")

    # rollback
    p_rollback = change_subparsers.add_parser("rollback", help="Roll back a multi-file run")
    p_rollback.add_argument("run_id", help="Run ID to roll back")
    p_rollback.add_argument("--stage", help="Specific stage ID to roll back (default: full)")
    p_rollback.add_argument("--repo-root", default=".", help="Repository root directory")


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------


def _cmd_analyze(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    analyzer = ImpactAnalyzer(repo_root)

    cc = ContractChange(
        contract_id=f"cmd-analyze-{args.symbol}",
        contract_type=ContractType.PUBLIC_FUNCTION,
        definition=SymbolReference(path=args.path, symbol=args.symbol),
        current_contract=f"{args.symbol}()",
        proposed_contract=f"{args.symbol}() [proposed]",
        scope=ContractScope.REPOSITORY_PUBLIC,
    )
    report = analyzer.analyze([cc])
    print(json.dumps(report.to_dict(), indent=2))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    file_path = Path(args.changeset_file)
    if not file_path.exists():
        print(f"Error: Change set file not found: {file_path}", file=sys.stderr)
        return 1

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        cs = EngineeringChangeSet.from_dict(data)
    except Exception as exc:
        print(f"Error reading change set JSON: {exc}", file=sys.stderr)
        return 1

    validator = ChangeSetConsistencyValidator(repo_root)
    result = validator.validate(cs)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.is_passing() else 1


def _cmd_execute(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    file_path = Path(args.changeset_file)
    if not file_path.exists():
        print(f"Error: Change set file not found: {file_path}", file=sys.stderr)
        return 1

    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        cs = EngineeringChangeSet.from_dict(data)
    except Exception as exc:
        print(f"Error reading change set JSON: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        validator = ChangeSetConsistencyValidator(repo_root)
        result = validator.validate(cs)
        print(f"[DRY RUN] Change Set Validation Result: {result.status.value}")
        return 0 if result.is_passing() else 1

    executor = StagedChangeSetExecutor(repo_root)
    res = executor.execute(cs)
    print(f"Execution Status: {res.status}")
    if res.failure_reason:
        print(f"Failure Reason: {res.failure_reason}")
    return 0 if res.status == "COMPLETED" else 1


def _cmd_status(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    run_dir = repo_root / ".nexus" / "runs" / args.run_id
    persistence = ChangeSetPersistence(run_dir)
    status_info = persistence.prepare_resume(args.run_id, str(repo_root))
    print(json.dumps(status_info, indent=2))
    return 0


def _cmd_rollback(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    run_dir = repo_root / ".nexus" / "runs" / args.run_id
    persistence = ChangeSetPersistence(run_dir)
    cs = persistence.load_change_set(args.run_id)
    if not cs:
        print(f"Error: No run found with ID '{args.run_id}'", file=sys.stderr)
        return 1

    executor = StagedChangeSetExecutor(repo_root, run_dir=run_dir)
    if args.stage:
        ok = executor.rollback_stage(cs, args.stage)
        print(f"Stage '{args.stage}' rollback: {'SUCCESS' if ok else 'FAILED'}")
    else:
        ok = executor.rollback_full(cs)
        print(f"Full change set rollback: {'SUCCESS' if ok else 'FAILED'}")
    return 0 if ok else 1
