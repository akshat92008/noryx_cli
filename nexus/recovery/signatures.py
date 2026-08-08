"""
Attempt Signatures and Loop Prevention Engine for Nexus CLI Recovery Subsystem.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass
class AttemptSignature:
    plan_version: int
    strategy_type: str
    model: str
    selected_context_hash: str
    target_files: list[str]
    patch_digest: str
    command: str
    repo_state_hash: str
    failure_category: str

    def digest(self) -> str:
        raw = f"{self.plan_version}|{self.strategy_type}|{self.model}|{self.selected_context_hash}|{sorted(self.target_files)}|{self.patch_digest}|{self.command}|{self.repo_state_hash}|{self.failure_category}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class LoopDetector:
    """Detects repeated failed strategies, patch oscillations, and infinite retry loops."""

    def __init__(self, max_consecutive_identical: int = 2, max_oscillations: int = 4, max_history: int = 10, **kwargs: Any) -> None:
        self.max_consecutive_identical = max_consecutive_identical
        self.max_oscillations = max_oscillations
        self.max_history = max_history
        self._history: list[AttemptSignature] = []
        self._digests: list[str] = []

    def record_attempt(self, signature: AttemptSignature) -> None:
        self._history.append(signature)
        self._digests.append(signature.digest())

    def is_looping(self, signature: AttemptSignature) -> tuple[bool, str]:
        current_digest = signature.digest()

        # Check exact repeated signature
        if self._digests.count(current_digest) >= self.max_consecutive_identical:
            return True, f"Identical recovery attempt strategy '{signature.strategy_type}' repeated {self.max_consecutive_identical} times without new evidence."

        # Check consecutive identical commands on unchanged repo state
        if len(self._history) >= 2:
            last = self._history[-1]
            if (
                last.command == signature.command
                and last.repo_state_hash == signature.repo_state_hash
                and last.patch_digest == signature.patch_digest
                and signature.command != ""
            ):
                return True, f"Command '{signature.command}' executed again on unchanged repository state."

        # Check oscillation (A -> B -> A -> B)
        if len(self._digests) >= 3:
            recent = self._digests[-3:]
            if recent[0] == current_digest:
                return True, "Oscillation detected between two failing recovery strategies."

        return False, ""
