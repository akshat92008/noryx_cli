"""
User Intervention Formatting and Triggers for Nexus CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserInterventionRequest:
    request_id: str
    reason: str
    failure_summary: str
    recommended_action: str
    alternatives: list[str] = field(default_factory=list)
    requires_approval: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def format_cli_prompt(self) -> str:
        alts = "\n  - ".join(self.alternatives) if self.alternatives else "None"
        return (
            f"⚠️ USER ACTION REQUIRED [{self.request_id}]\n"
            f"Reason: {self.reason}\n"
            f"Failure: {self.failure_summary}\n"
            f"Recommended: {self.recommended_action}\n"
            f"Alternatives:\n  - {alts}\n"
        )


class UserInterventionManager:
    """Constructs explicit user intervention requests."""

    @classmethod
    def create_request(
        cls,
        reason: str,
        failure_summary: str,
        recommended_action: str,
        alternatives: list[str] | None = None,
    ) -> UserInterventionRequest:
        req_id = f"req-{hash(reason + failure_summary) & 0xFFFFFFFF:08x}"
        return UserInterventionRequest(
            request_id=req_id,
            reason=reason,
            failure_summary=failure_summary,
            recommended_action=recommended_action,
            alternatives=alternatives or ["Cancel task", "Retry with manually expanded scope"],
        )
