"""Verified rollback decision and execution for Nexus recovery."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from nexus.history import FileHistory

logger = logging.getLogger(__name__)


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _read_json(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


class RollbackManager:
    """Rollback exactly one durable run and verify the restored filesystem state.

    A run may be rolled back only when its recorded history range is the newest
    range in the session.  This prevents rolling back an old turn through newer
    user-approved changes.
    """

    @staticmethod
    def rollback(run_id: str, *, force: bool = False) -> tuple[bool, str]:
        try:
            from nexus.run_catalog import RunCatalog

            catalog = RunCatalog()
            try:
                run_path = catalog.resolve(run_id)
            except Exception as exc:  # noqa: BLE001
                return False, f"Could not resolve run '{run_id}': {exc}"
            if not isinstance(run_path, Path) or not run_path.is_dir():
                return False, f"Could not resolve run '{run_id}'"

            session_id = run_path.parent.name
            if not session_id:
                return False, f"Rollback evidence for run '{run_id}' is invalid"

            final_report = _read_json(run_path / "final_report.json") or _read_json(
                run_path / "final-report.json"
            )
            request = _read_json(run_path / "request.json")
            report_metadata = final_report.get("metadata", {})
            request_metadata = request.get("metadata", {})
            if not isinstance(report_metadata, dict):
                report_metadata = {}
            if not isinstance(request_metadata, dict):
                request_metadata = {}

            start_raw = report_metadata.get(
                "history_start", request_metadata.get("history_start")
            )
            end_raw = report_metadata.get("history_end")
            try:
                history_start = int(start_raw)
                history_end = int(end_raw)
            except (TypeError, ValueError):
                return False, (
                    "Rollback blocked: durable history_start/history_end evidence "
                    f"is missing for run '{run_id}'."
                )

            history = FileHistory(session_id)
            total_changes = len(history.changes)
            if not (0 <= history_start <= history_end <= total_changes):
                return False, (
                    "Rollback blocked: recorded history range "
                    f"[{history_start}, {history_end}) is inconsistent with "
                    f"{total_changes} persisted change(s)."
                )
            if history_start == history_end:
                return False, "No changes were recorded for this run."
            if history_end != total_changes and not force:
                return False, (
                    "Rollback blocked: newer persisted changes exist after this run. "
                    "Rollback the newest run first."
                )

            selected = [dict(item) for item in history.changes[history_start:history_end]]
            expected: list[tuple[Path, bool, str | None]] = []
            for change in selected:
                target = Path(str(change.get("filepath", ""))).expanduser()
                if not target.is_absolute():
                    return False, "Rollback blocked: history contains a non-absolute target path."
                if bool(change.get("is_new_file")):
                    expected.append((target, False, None))
                    continue
                snapshot_value = change.get("snapshot_path")
                snapshot = Path(str(snapshot_value)).expanduser() if snapshot_value else None
                if snapshot is None or not snapshot.is_file():
                    return False, (
                        "Rollback blocked: required snapshot is unavailable for "
                        f"{target}."
                    )
                expected.append((target, True, _digest(snapshot)))

            count = history_end - history_start
            success, detail = history.undo_changes(count)
            if not success:
                return False, f"Rollback failed: {detail}"
            if len(history.changes) != history_start:
                return False, (
                    "Rollback could not be verified: persisted history length did not "
                    "return to the recorded start boundary."
                )

            verification_errors: list[str] = []
            for target, should_exist, expected_digest in expected:
                if should_exist:
                    if not target.is_file():
                        verification_errors.append(f"missing restored file: {target}")
                    elif expected_digest and _digest(target) != expected_digest:
                        verification_errors.append(f"restored content mismatch: {target}")
                elif target.exists():
                    verification_errors.append(f"new file still exists: {target}")
            if verification_errors:
                return False, "Rollback verification failed: " + "; ".join(verification_errors)

            return True, f"Verified rollback of {count} operation(s) for {session_id}/{run_path.name}:\n{detail}"
        except Exception as exc:  # noqa: BLE001
            return False, f"Rollback error: {exc}"


@dataclass
class RollbackDecision:
    should_rollback: bool
    immediate: bool
    reason: str
    target_checkpoint: str = ""


class RollbackDecisionEngine:
    """Enforces workspace integrity and executes verified rollbacks."""

    @classmethod
    def evaluate(
        cls,
        *,
        out_of_scope_mutation: bool = False,
        protected_path_changed: bool = False,
        syntax_broken: bool = False,
        new_regression: bool = False,
        corrupted_state: bool = False,
        unverified_patch: bool = False,
    ) -> RollbackDecision:
        if protected_path_changed:
            return RollbackDecision(
                should_rollback=True,
                immediate=True,
                reason="Mutation touched a protected system path.",
            )
        if out_of_scope_mutation:
            return RollbackDecision(
                should_rollback=True,
                immediate=True,
                reason="Mutation occurred outside the approved step scope.",
            )
        if syntax_broken:
            return RollbackDecision(
                should_rollback=True,
                immediate=True,
                reason="Patch broke syntax or module parsing across the repository.",
            )
        if corrupted_state:
            return RollbackDecision(
                should_rollback=True,
                immediate=True,
                reason="Workspace or repository state was left corrupted by attempt.",
            )
        if new_regression:
            return RollbackDecision(
                should_rollback=True,
                immediate=False,
                reason="Patch introduced a new regression in non-target tests.",
            )
        if unverified_patch:
            return RollbackDecision(
                should_rollback=True,
                immediate=False,
                reason="Patch has no valid verification evidence.",
            )

        return RollbackDecision(
            should_rollback=False,
            immediate=False,
            reason="Patch preserves repository integrity.",
        )

    @classmethod
    def execute_rollback(cls, run_id: str, working_dir: str = "") -> tuple[bool, str]:
        """Execute and independently verify a durable run rollback."""
        try:
            success, detail = RollbackManager.rollback(run_id)
            if success:
                logger.info("Rollback succeeded for run '%s': %s", run_id, detail)
                return True, f"Rollback verified: {detail}"
            logger.warning("Rollback failed for run '%s': %s", run_id, detail)
            return False, detail
        except Exception as exc:  # noqa: BLE001
            logger.error("Exception during rollback for run '%s': %s", run_id, exc)
            return False, f"Rollback execution error: {exc}"
