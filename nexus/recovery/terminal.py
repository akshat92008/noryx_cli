"""
Terminal States Governance for Nexus CLI Recovery Subsystem.
"""

from __future__ import annotations

from enum import Enum


class TerminalState(str, Enum):
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    VERIFICATION_UNAVAILABLE = "VERIFICATION_UNAVAILABLE"


class TerminalStateGovernance:
    """Enforces rules regarding terminal state authority."""

    @classmethod
    def validate_terminal_state(
        cls, proposed: TerminalState, is_canonical_verifier: bool = False
    ) -> TerminalState:
        if proposed == TerminalState.VERIFIED and not is_canonical_verifier:
            raise PermissionError(
                "Recovery subsystem cannot issue VERIFIED state directly. Only canonical finalizer can verify."
            )
        return proposed
