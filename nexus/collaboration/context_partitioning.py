"""
nexus/collaboration/context_partitioning.py

ContextPartitioner: builds minimal WorkerContextPacket for each worker,
ensuring workers receive only the context required for their assignment.

Rules enforced:
  - No full parent context passed to workers.
  - Secrets excluded.
  - Unrelated packages excluded.
  - Prohibited paths excluded.
  - Content hashes tracked for freshness validation.
  - Additional context requests routed through orchestrator.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from nexus.collaboration.models import (
    AgentAssignment,
    ContextResource,
    WorkerContextPacket,
)

# Pattern prefixes that indicate sensitive content
_SECRET_INDICATORS = (
    "api_key", "secret", "password", "token", "credential",
    "private_key", "auth", "bearer", "client_secret",
)


def _looks_like_secret_key(key: str) -> bool:
    k = key.lower()
    return any(ind in k for ind in _SECRET_INDICATORS)


def _hash_content(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


class ContextPartitionError(ValueError):
    pass


class ContextPartitioner:
    """
    Builds WorkerContextPackets from parent context slices.
    Performance target: < 100 ms excluding file I/O.
    """

    def __init__(self, repository_revision: str) -> None:
        self._revision = repository_revision

    def build_packet(
        self,
        assignment: AgentAssignment,
        parent_objective: str,
        available_resources: Sequence[Dict[str, Any]],
        relevant_evidence: Sequence[str] = (),
        dependency_summary: str = "",
        parent_constraints: Sequence[str] = (),
    ) -> WorkerContextPacket:
        """
        Build a minimal context packet for the worker.

        available_resources: list of dicts with keys:
            kind        str  ("file", "symbol", "test", "requirement")
            path        str
            content     str  (raw content — will be hashed, not forwarded verbatim)
            package     str  (package name for filtering)
        """
        start = time.monotonic()

        allowed_paths_set: set[Path] = set(assignment.allowed_paths)
        prohibited_paths_set: set[Path] = set(assignment.prohibited_paths)

        # Determine allowed packages from allowed_paths
        allowed_packages = {p.parts[0] if p.parts else "" for p in allowed_paths_set}

        filtered: List[ContextResource] = []
        token_estimate = 0

        for res in available_resources:
            path_str: str = res.get("path", "")
            res_path = Path(path_str) if path_str else None
            kind = res.get("kind", "file")
            package = res.get("package", "")
            content = res.get("content", "")

            # Skip prohibited paths
            if res_path and any(
                res_path == pp or _is_subpath(res_path, pp)
                for pp in prohibited_paths_set
            ):
                continue

            # Skip paths outside allowed scope (if assignment restricts paths)
            if res_path and allowed_paths_set:
                if not any(
                    res_path == ap or _is_subpath(res_path, ap)
                    for ap in allowed_paths_set
                ):
                    continue

            # Exclude unrelated packages
            if package and allowed_packages and package not in allowed_packages and "*" not in allowed_packages:
                continue

            # Exclude secrets from content
            if kind == "requirement" and _looks_like_secret_key(path_str):
                continue

            content_hash = _hash_content(content) if content else None
            token_estimate += max(1, len(content) // 4)

            filtered.append(ContextResource(
                resource_id=f"{kind}:{path_str}",
                kind=kind,
                path=path_str or None,
                content_hash=content_hash,
            ))

        # Merge parent constraints with assignment requirements
        combined_constraints = tuple(parent_constraints) + tuple(
            f"requirement:{r}" for r in assignment.requirements
        ) + tuple(
            f"prohibited:{p}" for p in assignment.prohibited_paths
        )

        # Output schema derived from expected_outputs
        output_schema = json.dumps({
            "expected_outputs": list(assignment.expected_outputs),
            "verification_requirements": list(assignment.verification_requirements),
        })

        elapsed_ms = (time.monotonic() - start) * 1000
        if elapsed_ms > 100:
            import logging
            logging.getLogger(__name__).warning(
                "ContextPartitioner exceeded 100 ms target (%.1f ms).", elapsed_ms
            )

        return WorkerContextPacket(
            assignment_id=assignment.assignment_id,
            objective=assignment.objective,
            role=assignment.role,
            constraints=combined_constraints,
            allowed_resources=tuple(filtered),
            dependency_summary=dependency_summary,
            relevant_evidence=tuple(relevant_evidence),
            expected_output_schema=output_schema,
            token_count=token_estimate,
            repository_revision=self._revision,
        )

    def validate_freshness(
        self,
        packet: WorkerContextPacket,
        current_revision: str,
    ) -> bool:
        """
        Returns True if packet is still fresh (revision unchanged).
        If repository has drifted, the packet must be regenerated.
        """
        return packet.repository_revision == current_revision

    def build_additional_context_response(
        self,
        request_kind: str,
        resource_path: str,
        content: str,
        assignment: AgentAssignment,
    ) -> Optional[ContextResource]:
        """
        Orchestrator-mediated additional context grant.
        Validates that the requested resource falls within allowed_paths.
        Returns None if the request is denied.
        """
        requested = Path(resource_path)
        allowed = assignment.allowed_paths
        prohibited = assignment.prohibited_paths

        if not allowed:
            return None  # No allowed paths defined — deny

        if any(_is_subpath(requested, pp) or requested == pp for pp in prohibited):
            return None  # Explicitly prohibited

        if not any(_is_subpath(requested, ap) or requested == ap for ap in allowed):
            return None  # Outside allowed scope

        content_hash = _hash_content(content)
        return ContextResource(
            resource_id=f"{request_kind}:{resource_path}",
            kind=request_kind,
            path=resource_path,
            content_hash=content_hash,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_subpath(candidate: Path, parent: Path) -> bool:
    """True if candidate is nested under parent."""
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False
