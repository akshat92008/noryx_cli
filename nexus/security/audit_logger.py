"""Append-only corruption-evident audit logger for Noryx CLI.

Records security decisions, policy evaluations, secret redactions, and approvals.
The local SHA-256 chain detects accidental or unsophisticated modification; it is not
an adversarial signature unless the log is anchored outside the writable workspace.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from nexus.security.secret_protection import SecretRedactor


@dataclass
class AuditEvent:
    event_id: str
    run_id: str
    actor: str
    event_type: str
    action: str
    outcome: str
    target: str
    details: dict[str, Any]
    timestamp: float
    prev_hash: str
    event_hash: str = ""

    def calculate_hash(self) -> str:
        payload = f"{self.event_id}:{self.run_id}:{self.actor}:{self.event_type}:{self.action}:{self.outcome}:{self.target}:{json.dumps(self.details, sort_keys=True)}:{self.timestamp:.6f}:{self.prev_hash}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class AuditLogger:
    """Append-only, hash-chained audit event logger."""

    def __init__(self, run_dir: str | Path, run_id: str = "run-default", actor: str = "agent"):
        self.run_dir = Path(run_dir).expanduser().resolve()
        self.security_dir = self.run_dir / "security"
        self.audit_dir = self.run_dir / "audit"
        self.security_dir.mkdir(parents=True, exist_ok=True)
        self.audit_dir.mkdir(parents=True, exist_ok=True)

        self.run_id = run_id
        self.actor = actor
        self.audit_file = self.audit_dir / "audit.jsonl"
        self.redactor = SecretRedactor()
        self.last_hash = "GENESIS_00000000000000000000000000000000"
        self._init_chain()

    def _init_chain(self) -> None:
        """Initialize or recover the last hash from existing log file."""
        if self.audit_file.exists() and self.audit_file.stat().st_size > 0:
            lines = self.audit_file.read_text(encoding="utf-8").strip().splitlines()
            if lines:
                try:
                    last_obj = json.loads(lines[-1])
                    self.last_hash = last_obj.get("event_hash", self.last_hash)
                except Exception:
                    pass

    def log_event(
        self,
        event_type: str,
        action: str,
        outcome: str,
        target: str = "",
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Record a structured security event with SHA-256 hash chaining."""
        event_id = f"evt-{hashlib.md5(f'{time.time()}:{action}:{target}'.encode('utf-8')).hexdigest()[:12]}"
        safe_details = self.redactor.redact_object(details or {})

        event = AuditEvent(
            event_id=event_id,
            run_id=self.run_id,
            actor=self.actor,
            event_type=event_type,
            action=action,
            outcome=outcome,
            target=target,
            details=safe_details,
            timestamp=time.time(),
            prev_hash=self.last_hash,
        )
        event.event_hash = event.calculate_hash()
        self.last_hash = event.event_hash

        line = json.dumps(asdict(event), sort_keys=True) + "\n"
        with open(self.audit_file, "a", encoding="utf-8") as f:
            f.write(line)

        return event


class AuditIntegrityVerifier:
    """Verifies audit log SHA-256 hash-chain integrity."""

    @staticmethod
    def verify(audit_file: str | Path) -> tuple[bool, str]:
        path = Path(audit_file).expanduser().resolve()
        if not path.exists() or path.stat().st_size == 0:
            return True, "Audit file is empty or missing"

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        expected_prev = "GENESIS_00000000000000000000000000000000"

        for idx, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except Exception:
                return False, f"Line {idx} is invalid JSON"

            if data.get("prev_hash") != expected_prev:
                return False, f"Line {idx} hash chain break: prev_hash mismatch"

            evt = AuditEvent(
                event_id=data["event_id"],
                run_id=data["run_id"],
                actor=data["actor"],
                event_type=data["event_type"],
                action=data["action"],
                outcome=data["outcome"],
                target=data["target"],
                details=data.get("details", {}),
                timestamp=float(data["timestamp"]),
                prev_hash=data["prev_hash"],
            )
            calc = evt.calculate_hash()
            if calc != data.get("event_hash"):
                return False, f"Line {idx} content tamper detected: hash mismatch"

            expected_prev = data["event_hash"]

        return True, f"Verified {len(lines)} audit events cleanly"
