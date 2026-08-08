"""Persistent, machine-checkable evidence for every Nexus completion claim."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus.paths import nexus_home
from nexus.storage import exclusive_file_lock, read_jsonl_prefix, recover_jsonl_suffix


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_fingerprint(path: str | Path) -> dict[str, Any]:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return {"path": str(p), "exists": False, "sha256": None, "size": 0}
    data = p.read_bytes()
    return {
        "path": str(p),
        "exists": True,
        "sha256": sha256_bytes(data),
        "size": len(data),
    }


@dataclass
class EvidenceRecord:
    id: str
    timestamp_utc: str
    session_id: str
    kind: str
    claim: str
    status: str
    tool: str = ""
    command: str = ""
    exit_code: int | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    raw_output: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class EvidenceTrail:
    """Append-only JSONL evidence trail scoped to a session."""

    def __init__(self, session_id: str, root: str | Path | None = None):
        self.session_id = session_id
        base = Path(root).resolve() if root else nexus_home()
        self.directory = base / "evidence"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / f"{session_id}.jsonl"
        records, self._corruption = read_jsonl_prefix(self.path)
        self._counter = len(records)

    def append(
        self,
        *,
        kind: str,
        claim: str,
        status: str,
        tool: str = "",
        command: str = "",
        exit_code: int | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        raw_output: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceRecord:
        with exclusive_file_lock(self.path):
            records, corruption = read_jsonl_prefix(self.path)
            if corruption:
                backup = recover_jsonl_suffix(self.path, records)
                recovery_record = EvidenceRecord(
                    id=f"ev-{len(records) + 1:06d}",
                    timestamp_utc=datetime.now(timezone.utc).isoformat(),
                    session_id=self.session_id,
                    kind="storage_corruption",
                    claim="Recovered the valid evidence prefix and quarantined a corrupt suffix",
                    status="failed",
                    raw_output=corruption["error"],
                    metadata={**corruption, "backup": str(backup)},
                )
                self._write_record(recovery_record)
                records.append(asdict(recovery_record))
            self._counter = len(records) + 1
            record = EvidenceRecord(
                id=f"ev-{self._counter:06d}",
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                session_id=self.session_id,
                kind=kind,
                claim=claim,
                status=status,
                tool=tool,
                command=command,
                exit_code=exit_code,
                artifacts=artifacts or [],
                raw_output=raw_output,
                metadata=metadata or {},
            )
            self._write_record(record)
        return record

    def _write_record(self, record: EvidenceRecord) -> None:
        encoded = (json.dumps(asdict(record), ensure_ascii=False) + "\n").encode("utf-8")
        descriptor = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, encoded)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def records(self, limit: int | None = None) -> list[dict[str, Any]]:
        records = list(self._iter_records())
        if self._corruption:
            records.append(
                {
                    "id": f"ev-corrupt-{self._corruption['line']}",
                    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                    "session_id": self.session_id,
                    "kind": "storage_corruption",
                    "claim": "Evidence JSONL contains a corrupt suffix",
                    "status": "failed",
                    "raw_output": self._corruption["error"],
                    "metadata": dict(self._corruption),
                }
            )
        return records[-limit:] if limit else records

    def verify_recent(self, count: int = 10) -> tuple[bool, str]:
        """Re-read file artifacts and report any drift from recorded hashes."""
        chosen = self.records(max(1, count))
        if not chosen:
            return False, "No evidence records exist for this session."
        lines = [f"Evidence re-verification: {len(chosen)} record(s)"]
        all_match = True
        for record in chosen:
            discrepancies: list[str] = []
            checked_artifact = False
            for artifact in record.get("artifacts", []):
                if not isinstance(artifact, dict) or not artifact.get("path"):
                    discrepancies.append("malformed artifact evidence")
                    continue
                expected = artifact.get("sha256")
                expected_exists = artifact.get("exists")
                if expected is None and expected_exists is None:
                    continue
                checked_artifact = True
                artifact_path = Path(artifact["path"]).expanduser().resolve()
                if artifact.get("kind") == "symlink" and artifact_path.is_symlink():
                    target = os.readlink(artifact_path)
                    current = {
                        "path": str(artifact_path),
                        "exists": True,
                        "sha256": sha256_bytes(
                            target.encode("utf-8", errors="surrogateescape")
                        ),
                        "size": len(target.encode("utf-8", errors="surrogateescape")),
                    }
                else:
                    current = file_fingerprint(artifact_path)
                if expected_exists is not None and current.get("exists") != expected_exists:
                    discrepancies.append(
                        f"{artifact['path']}: expected exists={expected_exists}, "
                        f"got exists={current.get('exists')}"
                    )
                    continue
                if expected is not None and current.get("sha256") != expected:
                    discrepancies.append(
                        f"{artifact['path']}: expected {expected}, got {current.get('sha256')}"
                    )
            if discrepancies:
                all_match = False
                lines.append(f"  FAIL {record['id']} {record['claim']}")
                lines.extend(f"    {item}" for item in discrepancies)
            elif checked_artifact:
                lines.append(f"  MATCH {record['id']} {record['claim']}")
            elif record.get("kind") == "command":
                lines.append(f"  RERUN_REQUIRED {record['id']} {record['claim']}")
            else:
                lines.append(f"  RECORDED {record['id']} {record['claim']}")
        lines.append(f"Raw trail: {self.path}")
        return all_match, "\n".join(lines)

    def _iter_records(self):
        records, self._corruption = read_jsonl_prefix(self.path)
        return iter(records)


_EXIT_CODE = re.compile(r"^❌ \(exit code (\d+)\)", re.MULTILINE)


def command_exit_code(result: str) -> int | None:
    """Extract the literal exit code emitted by Nexus' command tool."""
    if re.search(r"^✅ \$", result, re.MULTILINE):
        return 0
    match = _EXIT_CODE.search(result)
    return int(match.group(1)) if match else None


def verify_mutation(
    tool: str, args: dict[str, Any], working_dir: str | None = None
) -> tuple[bool, str, list[dict[str, Any]]]:
    """Re-read a mutation target and compare it with the requested intent."""
    if tool == "multi_edit":
        artifacts: list[dict[str, Any]] = []
        failures: list[str] = []
        for edit in args.get("edits", []):
            ok, detail, found = verify_mutation("edit_file", edit, working_dir)
            artifacts.extend(found)
            if not ok:
                failures.append(detail)
        return not failures, "; ".join(failures) or "all edits re-read and matched", artifacts

    raw_path = args.get("path") or args.get("file_path")
    if not raw_path:
        return False, "mutation did not identify a target path", []

    path = Path(raw_path).expanduser()
    if not path.is_absolute() and working_dir:
        path = Path(working_dir) / path
    path = path.resolve()
    artifact = file_fingerprint(path)
    if not artifact["exists"]:
        return False, f"target missing after write: {path}", [artifact]
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return False, f"could not re-read {path}: {exc}", [artifact]

    if tool == "write_file":
        expected = str(args.get("content", ""))
        ok = text == expected
        return (
            ok,
            "exact content matched" if ok else "disk content differs from requested content",
            [artifact],
        )
    if tool == "edit_file":
        new_text = str(args.get("new_text", ""))
        ok = new_text in text
        return (
            ok,
            "replacement text found on disk" if ok else "replacement text missing after edit",
            [artifact],
        )
    if tool == "patch_file":
        new_content = str(args.get("new_content", ""))
        ok = not new_content or new_content in text
        return (
            ok,
            "patched content found on disk" if ok else "patched content missing after edit",
            [artifact],
        )
    return False, f"unsupported mutation verifier: {tool}", [artifact]
