"""Hash-chained failure learning for recovery strategy selection."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus.intelligence.engineering.integrity import StateAuthenticator
from nexus.storage import exclusive_file_lock, read_jsonl_prefix


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(text: str) -> str:
    value = re.sub(r"(?i)(api[_-]?key|token|password|secret)\s*[=:]\s*\S+", r"\1=[REDACTED]", text)
    return re.sub(r"\b(?:sk|ghp|gsk|nvapi)[_-][A-Za-z0-9_-]{8,}\b", "[REDACTED]", value)


def _fingerprint(category: str, phase: str, summary: str) -> str:
    normalized = re.sub(r"\b\d+\b", "#", summary.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()[:1000]
    return hashlib.sha256(f"{category}|{phase}|{normalized}".encode()).hexdigest()


class FailureLearningIntegrityError(ValueError):
    """Raised when the append-only failure lesson chain is corrupt."""


@dataclass(frozen=True)
class FailureLesson:
    fingerprint: str
    category: str
    phase: str
    summary: str
    occurrence: int
    recommended_strategy: str
    previous_hash: str
    record_hash: str
    created_at: str
    key_id: str
    scheme: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class FailureLearningStore:
    """Append-only failure lessons authenticated by a repository-external HMAC key."""

    def __init__(self, repository_root: str | Path):
        root = Path(repository_root).expanduser().resolve()
        self.path = root / ".nexus" / "failure-lessons.v2.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.authenticator = StateAuthenticator.for_repository(root)

    def _records(self) -> list[dict[str, Any]]:
        records, corruption = read_jsonl_prefix(self.path)
        if corruption is not None:
            raise FailureLearningIntegrityError(
                f"Failure-learning log is corrupt at line {corruption['line']}: "
                f"{corruption['error']}"
            )
        return records

    def _verify_records(self, records: list[dict[str, Any]]) -> bool:
        previous = "GENESIS"
        for item in records:
            record_hash = str(item.get("record_hash", ""))
            body = dict(item)
            body.pop("record_hash", None)
            if body.get("previous_hash") != previous:
                return False
            if not self.authenticator.verify(
                body,
                record_hash,
                key_id=str(body.get("key_id", "")),
                scheme=str(body.get("scheme", "")),
            ):
                return False
            previous = record_hash
        return True

    @staticmethod
    def _strategy(category: str, phase: str, occurrence: int) -> str:
        if occurrence >= 3:
            return "stop editing, rebuild the root-cause model, inspect callers and invariants, then escalate to the Ceiling"
        if category in {"test_failure", "verification"}:
            return "reproduce the narrowest failing case, compare pre/post behavior, and inspect the first causal stack frame"
        if category in {"scope", "architecture"}:
            return "rebuild repository impact analysis and revise the permitted-file contract before another mutation"
        if category in {"provider", "timeout"}:
            return "checkpoint state, reduce context, retry once, then switch provider without repeating completed work"
        if phase == "planning":
            return "collect missing repository evidence and re-run the independent plan critic"
        return "form a different root-cause hypothesis and run a discriminating read-only check before editing"

    def record(self, *, category: str, phase: str, summary: str) -> FailureLesson:
        safe_summary = _redact(summary.strip())[:2000]
        fp = _fingerprint(category, phase, safe_summary)
        with exclusive_file_lock(self.path):
            records = self._records()
            if not self._verify_records(records):
                raise FailureLearningIntegrityError(
                    "Failure-learning hash chain is invalid; refusing to append a new lesson."
                )
            occurrence = 1 + sum(1 for item in records if item.get("fingerprint") == fp)
            previous_hash = str(records[-1].get("record_hash", "")) if records else "GENESIS"
            created_at = _now()
            body = {
                "fingerprint": fp,
                "category": category,
                "phase": phase,
                "summary": safe_summary,
                "occurrence": occurrence,
                "recommended_strategy": self._strategy(category, phase, occurrence),
                "previous_hash": previous_hash,
                "created_at": created_at,
                "key_id": self.authenticator.key_id,
                "scheme": self.authenticator.scheme,
            }
            record_hash = self.authenticator.sign(body)
            lesson = FailureLesson(record_hash=record_hash, **body)
            descriptor = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                payload = (json.dumps(lesson.to_dict(), sort_keys=True) + "\n").encode("utf-8")
                os.write(descriptor, payload)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return lesson

    def verify_chain(self) -> bool:
        try:
            records = self._records()
        except FailureLearningIntegrityError:
            return False
        return self._verify_records(records)

    def recent_context(self, *, limit: int = 8) -> str:
        records = self._records()
        if not self._verify_records(records):
            raise FailureLearningIntegrityError(
                "Failure-learning authentication chain is invalid; refusing model context."
            )
        records = records[-max(1, limit) :]
        if not records:
            return ""
        lines = ["[REPOSITORY FAILURE LESSONS]"]
        for item in records:
            lines.append(
                f"- {item.get('category')}/{item.get('phase')} x{item.get('occurrence')}: "
                f"{item.get('summary')} -> {item.get('recommended_strategy')}"
            )
        return "\n".join(lines)
