"""
nexus/collaboration/conflicts.py

Mutation-scope reservations and semantic-conflict detection.

Rules:
  - Exclusive (WRITE) reservations cannot overlap.
  - SHARED_READ reservations may overlap.
  - Reservations expire and must be refreshed.
  - Out-of-reservation mutations are blocked.
  - Scope expansion requires orchestrator approval.
  - Semantic conflicts detected from API/type/config divergence signals.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from nexus.collaboration.models import (
    MutationScopeReservation,
    ReservationMode,
)

# ---------------------------------------------------------------------------
# Reservation errors
# ---------------------------------------------------------------------------


class ReservationConflictError(RuntimeError):
    """Raised when two exclusive reservations would overlap."""


class OutOfScopeError(RuntimeError):
    """Raised when a worker attempts a mutation outside its reservation."""


class StaleReservationError(RuntimeError):
    """Raised when a reservation has expired."""


# ---------------------------------------------------------------------------
# Scope reservation registry
# ---------------------------------------------------------------------------

_DEFAULT_TTL_SECONDS = 600  # 10 minutes


class ScopeReservationRegistry:
    """
    Central registry for mutation-scope reservations.
    Performance target: < 20 ms per check.
    """

    def __init__(self) -> None:
        self._reservations: Dict[str, MutationScopeReservation] = {}

    # ------------------------------------------------------------------
    # Reservation lifecycle
    # ------------------------------------------------------------------

    def reserve(
        self,
        assignment_id: str,
        paths: Tuple[Path, ...],
        symbol_ids: Tuple[str, ...],
        mode: ReservationMode,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> MutationScopeReservation:
        """
        Register a reservation.
        Raises ReservationConflictError if an exclusive reservation would overlap.
        """
        now = datetime.now(tz=timezone.utc)
        expires_at = now + timedelta(seconds=ttl_seconds)

        if mode == ReservationMode.EXCLUSIVE:
            # Check for existing exclusive overlaps
            for existing in self._active_reservations():
                if existing.mode == ReservationMode.EXCLUSIVE:
                    overlap = self._path_overlap(paths, existing.paths)
                    symbol_overlap = set(symbol_ids) & set(existing.symbol_ids)
                    if overlap or symbol_overlap:
                        raise ReservationConflictError(
                            f"Exclusive reservation for assignment '{assignment_id}' "
                            f"conflicts with existing reservation '{existing.reservation_id}' "
                            f"(assignment '{existing.assignment_id}'). "
                            f"Overlapping paths: {overlap}. Overlapping symbols: {symbol_overlap}."
                        )

        reservation = MutationScopeReservation(
            reservation_id=str(uuid.uuid4()),
            assignment_id=assignment_id,
            paths=paths,
            symbol_ids=symbol_ids,
            mode=mode,
            expires_at=expires_at,
        )
        self._reservations[reservation.reservation_id] = reservation
        return reservation

    def release(self, reservation_id: str) -> bool:
        """Release a reservation by ID. Returns True if it existed."""
        return self._reservations.pop(reservation_id, None) is not None

    def release_for_assignment(self, assignment_id: str) -> int:
        """Release all reservations for a given assignment. Returns count released."""
        to_remove = [
            rid for rid, r in self._reservations.items()
            if r.assignment_id == assignment_id
        ]
        for rid in to_remove:
            del self._reservations[rid]
        return len(to_remove)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_mutation(self, assignment_id: str, path: Path) -> None:
        """
        Ensures the mutation is within a valid exclusive reservation for this assignment.
        Raises OutOfScopeError or StaleReservationError.
        """
        now = datetime.now(tz=timezone.utc)
        owned = [
            r for r in self._reservations.values()
            if r.assignment_id == assignment_id and r.mode == ReservationMode.EXCLUSIVE
        ]

        if not owned:
            raise OutOfScopeError(
                f"Assignment '{assignment_id}' attempted mutation on '{path}' "
                "without any exclusive reservation."
            )

        for r in owned:
            if r.expires_at < now:
                raise StaleReservationError(
                    f"Reservation '{r.reservation_id}' for assignment '{assignment_id}' "
                    "has expired."
                )
            if any(_is_subpath(path, rp) or path == rp for rp in r.paths):
                return  # Valid

        raise OutOfScopeError(
            f"Assignment '{assignment_id}' attempted mutation on '{path}' "
            "which is outside all its reserved paths."
        )

    def detect_stale(self) -> List[MutationScopeReservation]:
        """Return all expired reservations without removing them."""
        now = datetime.now(tz=timezone.utc)
        return [r for r in self._reservations.values() if r.expires_at < now]

    def get_reservation(self, reservation_id: str) -> Optional[MutationScopeReservation]:
        return self._reservations.get(reservation_id)

    def list_active(self) -> List[MutationScopeReservation]:
        return self._active_reservations()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _active_reservations(self) -> List[MutationScopeReservation]:
        now = datetime.now(tz=timezone.utc)
        return [r for r in self._reservations.values() if r.expires_at >= now]

    @staticmethod
    def _path_overlap(
        a: Tuple[Path, ...], b: Tuple[Path, ...]
    ) -> List[Path]:
        overlapping: List[Path] = []
        for pa in a:
            for pb in b:
                if pa == pb or _is_subpath(pa, pb) or _is_subpath(pb, pa):
                    overlapping.append(pa)
        return overlapping


# ---------------------------------------------------------------------------
# Semantic conflict detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticConflict:
    conflict_id: str
    assignment_id_a: str
    assignment_id_b: str
    kind: str  # "api_assumption", "duplicate_symbol", "config_divergence", etc.
    description: str
    severity: str  # "blocking", "warning"
    affected_files: Tuple[str, ...]


@dataclass
class ChangeSignal:
    """Lightweight representation of what a worker intends to change."""
    assignment_id: str
    exported_symbols: List[str] = field(default_factory=list)
    modified_configs: List[str] = field(default_factory=list)
    api_assumptions: Dict[str, str] = field(default_factory=dict)  # symbol -> assumed_signature
    new_imports: List[str] = field(default_factory=list)
    affected_files: List[str] = field(default_factory=list)


class SemanticConflictAnalyser:
    """
    Detects semantic incompatibilities between proposed changes from different workers.
    Textual non-overlap does not guarantee semantic compatibility.
    """

    def analyse(
        self,
        signals: List[ChangeSignal],
    ) -> List[SemanticConflict]:
        conflicts: List[SemanticConflict] = []

        for i, a in enumerate(signals):
            for b in signals[i + 1:]:
                conflicts.extend(self._compare(a, b))

        return conflicts

    # ------------------------------------------------------------------

    def _compare(
        self, a: ChangeSignal, b: ChangeSignal
    ) -> List[SemanticConflict]:
        found: List[SemanticConflict] = []

        # 1. Duplicate symbol exports
        dup_symbols = set(a.exported_symbols) & set(b.exported_symbols)
        if dup_symbols:
            found.append(SemanticConflict(
                conflict_id=f"dup-sym-{a.assignment_id}-{b.assignment_id}",
                assignment_id_a=a.assignment_id,
                assignment_id_b=b.assignment_id,
                kind="duplicate_symbol",
                description=f"Both workers export conflicting symbols: {dup_symbols}",
                severity="blocking",
                affected_files=tuple(a.affected_files + b.affected_files),
            ))

        # 2. Conflicting API assumptions
        for symbol, sig_a in a.api_assumptions.items():
            if symbol in b.api_assumptions:
                sig_b = b.api_assumptions[symbol]
                if sig_a != sig_b:
                    found.append(SemanticConflict(
                        conflict_id=f"api-{a.assignment_id}-{b.assignment_id}-{symbol}",
                        assignment_id_a=a.assignment_id,
                        assignment_id_b=b.assignment_id,
                        kind="api_assumption",
                        description=(
                            f"Workers disagree on API signature for '{symbol}': "
                            f"A assumes '{sig_a}', B assumes '{sig_b}'."
                        ),
                        severity="blocking",
                        affected_files=tuple(a.affected_files + b.affected_files),
                    ))

        # 3. Divergent configuration changes
        conf_overlap = set(a.modified_configs) & set(b.modified_configs)
        if conf_overlap:
            found.append(SemanticConflict(
                conflict_id=f"conf-{a.assignment_id}-{b.assignment_id}",
                assignment_id_a=a.assignment_id,
                assignment_id_b=b.assignment_id,
                kind="config_divergence",
                description=f"Both workers modify shared configuration keys: {conf_overlap}",
                severity="blocking",
                affected_files=tuple(a.affected_files + b.affected_files),
            ))

        return found


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _is_subpath(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False
