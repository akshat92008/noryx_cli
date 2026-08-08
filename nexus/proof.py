"""Tamper-evident Nexus Proof receipts.

A receipt is not a model-authored narrative.  It is a canonical digest of the
final run report, deterministic evidence, budget usage, repository revision,
and file fingerprints.  VERIFIED is retained only when the receipt can point
to passing external checks and satisfied acceptance criteria.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from nexus import __version__
from nexus.evidence import file_fingerprint

SCHEMA = "nexus.proof.v2"
_SUPPORTED_SCHEMAS = {SCHEMA}
_IGNORED_TREE_PARTS = {
    ".git",
    ".nexus",
    ".nexusai",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
}


def _canonical_hash(data: Any) -> str:
    encoded = json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in _IGNORED_TREE_PARTS for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".log"}:
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
        digest.update(b"\0")
    return digest.hexdigest()


def _source_revision(root: Path) -> str:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        commit = ""
    return f"git:{commit}" if commit else f"tree:{_tree_hash(root)}"


def _check_passed(item: Mapping[str, Any]) -> bool:
    if "success" in item:
        return bool(item["success"])
    status = str(item.get("status", "")).strip().lower()
    exit_code = item.get("exit_code")
    return status in {"pass", "passed", "verified", "success", "ok"} and exit_code in {
        None,
        0,
    }


def _normalize_checks(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for item in report.get("checks", []) or []:
        if not isinstance(item, Mapping):
            continue
        checks.append(
            {
                "name": str(item.get("name") or item.get("command") or "verification"),
                "command": str(item.get("command", "")),
                "status": str(item.get("status", "")),
                "exit_code": item.get("exit_code"),
                "passed": _check_passed(item),
                "evidence_id": str(item.get("evidence_id", "")),
            }
        )
    return checks


def _criterion_passed(item: Mapping[str, Any]) -> bool:
    return str(item.get("status", "")).upper() in {"SATISFIED", "VERIFIED", "PASSED"}


def _normalize_criteria(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in report.get("acceptance_criteria", []) or []:
        if not isinstance(item, Mapping):
            continue
        normalized.append(
            {
                "description": str(item.get("criterion") or item.get("description") or ""),
                "status": str(item.get("status", "UNVERIFIED")),
                "passed": _criterion_passed(item),
                "evidence_ids": [str(value) for value in item.get("evidence_ids", []) or []],
                "detail": str(item.get("detail", "")),
            }
        )
    return normalized


def _cost_snapshot(report: Mapping[str, Any]) -> tuple[float, dict[str, Any]]:
    costs = dict(report.get("costs") or {})
    usage = costs.get("usage") if isinstance(costs.get("usage"), Mapping) else {}
    value = usage.get("estimated_cost_usd", costs.get("estimated_cost_usd", 0.0))
    try:
        actual_usd = max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        actual_usd = 0.0
    return actual_usd, costs


def _changed_file_fingerprints(root: Path, report: Mapping[str, Any]) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in report.get("files_changed", []) or []:
        value = raw.get("path") if isinstance(raw, Mapping) else raw
        if not value:
            continue
        path = Path(str(value))
        path = path if path.is_absolute() else root / path
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            relative = str(path)
        if relative in seen:
            continue
        seen.add(relative)
        fingerprint = file_fingerprint(path)
        fingerprint["path"] = relative
        changed.append(fingerprint)
    return sorted(changed, key=lambda item: str(item.get("path", "")))


def create_proof_receipt(
    *,
    session_id: str,
    workspace: str | Path,
    final_report: Mapping[str, Any],
    evidence_records: Iterable[Mapping[str, Any]],
    authorized_budget_inr: float | None = None,
    usd_to_inr: float = 85.0,
    routing_decision: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a canonical receipt and downgrade unsupported success claims."""
    root = Path(workspace).expanduser().resolve()
    report = dict(final_report)
    records = [dict(item) for item in evidence_records]
    checks = _normalize_checks(report)
    criteria = _normalize_criteria(report)
    files_changed = _changed_file_fingerprints(root, report)
    actual_usd, costs = _cost_snapshot(report)
    actual_inr = round(actual_usd * float(usd_to_inr), 6)

    requested_status = str(report.get("status", "UNVERIFIED")).upper()
    checks_pass = bool(checks) and all(item["passed"] for item in checks)
    criteria_pass = bool(criteria) and all(item["passed"] for item in criteria)
    evidence_present = bool(records)
    within_budget = authorized_budget_inr is None or actual_inr <= float(authorized_budget_inr) + 1e-9

    status = requested_status
    downgrade_reasons: list[str] = []
    if requested_status == "VERIFIED":
        if not checks_pass:
            downgrade_reasons.append("no complete passing external-check set")
        if not criteria_pass:
            downgrade_reasons.append("acceptance criteria are absent or not all satisfied")
        if not evidence_present:
            downgrade_reasons.append("no underlying evidence records")
        if downgrade_reasons:
            status = "PARTIALLY_VERIFIED"
    if not within_budget:
        status = "BLOCKED"
        downgrade_reasons.append("authorized budget was exceeded")

    metadata = report.get("metadata") if isinstance(report.get("metadata"), Mapping) else {}
    payload: dict[str, Any] = {
        "schema_version": SCHEMA,
        "nexus_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "session_id": str(session_id),
        "turn_id": str(report.get("turn_id", "")),
        "status": status,
        "claimed_status": requested_status,
        "outcome": str(report.get("outcome", "")),
        "objective": str(report.get("objective", "")),
        "workspace": str(root),
        "source_revision": _source_revision(root),
        "repository_state_sha256": _tree_hash(root),
        "models": [str(value) for value in report.get("model_providers", []) or []],
        "routing_decision": dict(routing_decision or {}),
        "budget": {
            "authorized_inr": authorized_budget_inr,
            "actual_usd": actual_usd,
            "actual_inr_estimate": actual_inr,
            "usd_to_inr": float(usd_to_inr),
            "within_authorized_budget": within_budget,
            "raw": costs,
        },
        "files_changed": files_changed,
        "acceptance_criteria": criteria,
        "checks": checks,
        "verification": {
            "checks_passed": checks_pass,
            "criteria_passed": criteria_pass,
            "evidence_present": evidence_present,
            "review_assurance": str(metadata.get("review_assurance", "none")),
            "downgrade_reasons": downgrade_reasons,
        },
        "remaining_risks": [str(value) for value in report.get("remaining_risks", []) or []],
        "rollback_checkpoint": str(
            report.get("rollback_checkpoint") or report.get("turn_id") or ""
        ),
        "evidence": {
            "record_count": len(records),
            "records_sha256": _canonical_hash(records),
            "final_report_sha256": _canonical_hash(report),
        },
    }
    payload["receipt_hash"] = _canonical_hash(payload)
    return payload


def write_proof_receipt(payload: Mapping[str, Any], path: str | Path) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def verify_proof_receipt(
    path: str | Path,
    *,
    verify_workspace_state: bool = False,
) -> tuple[bool, str]:
    """Verify receipt integrity and optionally compare current repository state."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid proof: {exc}"
    if not isinstance(data, dict):
        return False, "invalid proof: root must be a JSON object"
    claimed = str(data.pop("receipt_hash", ""))
    if data.get("schema_version") not in _SUPPORTED_SCHEMAS:
        return False, "receipt integrity or schema contract failed"
    if not claimed or claimed != _canonical_hash(data):
        return False, "receipt integrity or verification contract failed"

    if data.get("status") == "VERIFIED":
        verification = data.get("verification") or {}
        if not (
            verification.get("checks_passed")
            and verification.get("criteria_passed")
            and verification.get("evidence_present")
        ):
            return False, "VERIFIED receipt lacks required external evidence"

    if verify_workspace_state:
        workspace = Path(str(data.get("workspace", ""))).expanduser()
        if not workspace.is_dir():
            return False, "receipt workspace is unavailable"
        current = _tree_hash(workspace.resolve())
        if current != data.get("repository_state_sha256"):
            return False, "repository state no longer matches the receipt"

    return True, f"valid {data['schema_version']} receipt"
