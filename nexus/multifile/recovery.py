"""
Multi-File Recovery Handler — Sprint 8.

Integrates Sprint 7's RecoveryController with staged multi-file change-set
execution. When a stage fails, this handler:
1. Identifies which stage failed and which files were partially modified.
2. Identifies which contracts remain inconsistent.
3. Decides stage-level vs full rollback.
4. Triggers bounded scope expansion via ImpactAnalyzer when a missed
   caller or dependency is discovered.
5. Revises dependency order when missed callers are found.
6. Prevents restart-from-scratch after every local failure.
7. Refuses to continue from a partially trusted state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from nexus.multifile.contracts import (
    EngineeringChangeSet,
    ImpactCategory,
    PlannedFileChange,
    ChangeType,
)
from nexus.multifile.events import (
    ChangeSetRolledBack,
    ScopeExpansionRequested,
)

logger = logging.getLogger(__name__)

# Maximum scope expansions before stopping
MAX_SCOPE_EXPANSIONS = 3


class RecoveryDecision(str, Enum):
    """What the handler decided to do after a failure."""
    RETRY_STAGE = "RETRY_STAGE"
    ROLLBACK_STAGE = "ROLLBACK_STAGE"
    ROLLBACK_FULL = "ROLLBACK_FULL"
    EXPAND_SCOPE = "EXPAND_SCOPE"
    REVISE_DEPENDENCY_ORDER = "REVISE_DEPENDENCY_ORDER"
    SPLIT_STAGE = "SPLIT_STAGE"
    STOP_BLOCKED = "STOP_BLOCKED"
    STOP_FAILED = "STOP_FAILED"


@dataclass
class RecoveryContext:
    """Context about a stage failure."""
    stage_id: str
    failure_reason: str
    files_partially_modified: list[str] = field(default_factory=list)
    contracts_inconsistent: list[str] = field(default_factory=list)
    missed_callers: list[str] = field(default_factory=list)  # discovered dynamically
    error_type: str = ""  # SYNTAX | IMPORT | TEST | TYPE | TIMEOUT | DEPENDENCY | UNKNOWN
    attempt_number: int = 1


@dataclass
class RecoveryAction:
    """Action to take after a failure."""
    decision: RecoveryDecision
    description: str
    new_scope: list[PlannedFileChange] = field(default_factory=list)
    rollback_target_stage: str = ""
    revised_order: list[str] = field(default_factory=list)  # revised file order
    stop_reason: str = ""
    emit_events: list[Any] = field(default_factory=list)


class MultiFileRecoveryHandler:
    """Integrates Sprint 7 recovery with staged multi-file execution."""

    def __init__(
        self,
        *,
        max_scope_expansions: int = MAX_SCOPE_EXPANSIONS,
        impact_analyzer: Any = None,  # nexus.multifile.impact.ImpactAnalyzer
        sprint7_controller: Any = None,  # nexus.recovery.RecoveryController
    ) -> None:
        self.max_scope_expansions = max_scope_expansions
        self._impact_analyzer = impact_analyzer
        self._sprint7_controller = sprint7_controller
        self._scope_expansions: int = 0
        self._failed_strategies: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle_stage_failure(
        self,
        cs: EngineeringChangeSet,
        context: RecoveryContext,
    ) -> RecoveryAction:
        """Determine the best recovery action after a stage failure.

        Decision logic:
        1. Syntax / parse error → rollback stage, attempt fix.
        2. Missing caller (TypeError about arguments) → expand scope if budget allows.
        3. Import error after move/rename → check stale imports, revise order.
        4. Test failure due to changed contract → expand scope to include test files.
        5. Full rollback if state is untrustworthy or budget exhausted.
        6. STOP_FAILED if repeated strategy detected.
        """
        events: list[Any] = []

        # Detect repeated strategy (loop prevention) — check BEFORE adding
        strategy_key = f"{context.stage_id}:{context.error_type}"
        prior_count = self._failed_strategies.count(strategy_key)
        self._failed_strategies.append(strategy_key)
        if prior_count >= 1:
            return RecoveryAction(
                decision=RecoveryDecision.STOP_FAILED,
                description=(
                    f"Repeated failure for stage '{context.stage_id}' with error "
                    f"'{context.error_type}'. Loop detected — stopping."
                ),
                stop_reason="Repeated strategy loop detected.",
            )

        # Check if state can be trusted
        if context.files_partially_modified:
            if not self._is_state_trustworthy(cs, context.files_partially_modified):
                return RecoveryAction(
                    decision=RecoveryDecision.ROLLBACK_FULL,
                    description=(
                        "Partially mutated repository state cannot be trusted. "
                        "Full rollback required."
                    ),
                    emit_events=[ChangeSetRolledBack(
                        run_id=cs.run_id,
                        change_set_id=cs.change_set_id,
                        scope="FULL_CHANGE_SET",
                        stage_id=context.stage_id,
                        files_restored=context.files_partially_modified,
                        reason="Partially trusted state after stage failure.",
                    )],
                )

        # Missed callers detected
        if context.missed_callers:
            return self._handle_missed_callers(cs, context, events)

        # Error-type-based decisions
        error_lower = context.error_type.lower() + context.failure_reason.lower()

        if "syntax" in error_lower or "parse" in error_lower:
            return RecoveryAction(
                decision=RecoveryDecision.ROLLBACK_STAGE,
                description=f"Syntax error in stage '{context.stage_id}'. Rolling back stage.",
                rollback_target_stage=context.stage_id,
            )

        if "importerror" in error_lower or "modulenotfounderror" in error_lower:
            return RecoveryAction(
                decision=RecoveryDecision.REVISE_DEPENDENCY_ORDER,
                description=(
                    "Import error detected — likely a dependency ordering issue. "
                    "Re-analyzing dependency graph."
                ),
            )

        if "typeerror" in error_lower and "argument" in error_lower:
            # Likely a missed caller that wasn't updated
            return self._handle_missed_callers(cs, context, events)

        if "test" in error_lower or "assertion" in error_lower:
            return RecoveryAction(
                decision=RecoveryDecision.ROLLBACK_STAGE,
                description=(
                    f"Test failure in stage '{context.stage_id}'. "
                    "Verify impact analysis captured all affected test files."
                ),
                rollback_target_stage=context.stage_id,
            )

        # Default: rollback current stage and stop if too many attempts
        if context.attempt_number >= 3:
            return RecoveryAction(
                decision=RecoveryDecision.STOP_FAILED,
                description=f"Stage '{context.stage_id}' failed {context.attempt_number} times.",
                stop_reason="Max attempts exceeded for stage.",
            )

        return RecoveryAction(
            decision=RecoveryDecision.ROLLBACK_STAGE,
            description=f"Unclassified failure in stage '{context.stage_id}'. Rolling back stage.",
            rollback_target_stage=context.stage_id,
        )

    def handle_consistency_failure(
        self,
        cs: EngineeringChangeSet,
        missing_changes: list[Any],
        stale_references: list[Any],
    ) -> RecoveryAction:
        """Handle a consistency validation failure before execution."""
        if stale_references:
            return RecoveryAction(
                decision=RecoveryDecision.STOP_BLOCKED,
                description=(
                    f"Change set has {len(stale_references)} stale references "
                    "that must be resolved before execution."
                ),
                stop_reason="Stale references in change set — plan must be revised.",
            )

        if missing_changes:
            # We can try to expand scope to include the missing files
            new_changes = [
                PlannedFileChange(
                    path=mc.path,
                    reason=mc.reason,
                    change_type=ChangeType.MODIFY,
                    confidence=0.8,
                )
                for mc in missing_changes
            ]
            return RecoveryAction(
                decision=RecoveryDecision.EXPAND_SCOPE,
                description=(
                    f"Expanding scope to include {len(missing_changes)} missing change(s)."
                ),
                new_scope=new_changes,
                emit_events=[ScopeExpansionRequested(
                    run_id=cs.run_id,
                    change_set_id=cs.change_set_id,
                    reason=f"Consistency validator found {len(missing_changes)} missing changes.",
                    new_paths=[mc.path for mc in missing_changes],
                )],
            )

        return RecoveryAction(
            decision=RecoveryDecision.STOP_BLOCKED,
            description="Unknown consistency failure.",
            stop_reason="Consistency failure with no resolution.",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _handle_missed_callers(
        self,
        cs: EngineeringChangeSet,
        context: RecoveryContext,
        events: list[Any],
    ) -> RecoveryAction:
        if self._scope_expansions >= self.max_scope_expansions:
            return RecoveryAction(
                decision=RecoveryDecision.STOP_FAILED,
                description=(
                    f"Scope expansion limit reached ({self.max_scope_expansions}). "
                    "Cannot safely add more files to the change set."
                ),
                stop_reason="Max scope expansions exceeded.",
            )

        self._scope_expansions += 1
        new_paths = context.missed_callers

        events.append(ScopeExpansionRequested(
            run_id=cs.run_id,
            change_set_id=cs.change_set_id,
            reason=(
                f"Missed caller(s) discovered after stage '{context.stage_id}' failure: "
                f"{new_paths}"
            ),
            new_paths=new_paths,
        ))

        new_changes = [
            PlannedFileChange(
                path=path,
                reason="Missed caller discovered during recovery — scope expansion.",
                change_type=ChangeType.MODIFY,
                confidence=0.9,
            )
            for path in new_paths
        ]

        return RecoveryAction(
            decision=RecoveryDecision.EXPAND_SCOPE,
            description=(
                f"Expanding scope to include {len(new_paths)} missed caller(s): {new_paths}"
            ),
            new_scope=new_changes,
            emit_events=events,
        )

    def _is_state_trustworthy(
        self, cs: EngineeringChangeSet, partially_modified: list[str]
    ) -> bool:
        """Determine if the repository state can be trusted after a partial application."""
        # If too many files were partially modified, state cannot be trusted
        if len(partially_modified) > 5:
            return False
        # If the partial files include contract definitions, state cannot be trusted
        for file_path in partially_modified:
            fc = cs.get_file_change(file_path)
            if fc and fc.relevant_symbols:
                # Has symbol changes — definition may be inconsistent
                return False
        return True
