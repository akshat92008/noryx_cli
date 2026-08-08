"""Ambiguity and Clarification Engine (Sprint 6)."""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from nexus.planning.task_contract import Assumption, Question, TaskContract, TaskType


class ClarificationType(str, Enum):
    BLOCKING = "blocking"
    NON_BLOCKING = "non_blocking"
    RESOLVABLE_FROM_REPO = "resolvable_from_repo"


class AmbiguityEngine:
    """Detects ambiguities in user requests and governs clarification flow."""

    def __init__(self, repo_intelligence: Any = None):
        self.repo_intelligence = repo_intelligence

    def analyze(
        self, request: str, repo_context: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[Question], List[Assumption]]:
        """Analyze request for ambiguity, returning blocking questions or assumptions."""
        questions: List[Question] = []
        assumptions: List[Assumption] = []

        lower_req = request.lower()

        # Check 1: Mutually contradictory or destructive actions
        if "delete all" in lower_req or "drop table" in lower_req or "hard reset" in lower_req:
            questions.append(
                Question(
                    id="Q-BLOCK-DESTRUCTIVE",
                    text="The request includes a destructive operation. Confirm target and backup status.",
                    is_blocking=True,
                    category="safety",
                )
            )

        # Check 2: Missing target details for migrations or API changes
        if "migrate database" in lower_req and "schema" not in lower_req:
            questions.append(
                Question(
                    id="Q-BLOCK-MIGRATION",
                    text="Database migration requested without target schema details.",
                    is_blocking=True,
                    category="data_model",
                )
            )

        # Check 3: Check if question is resolvable from repo evidence
        repo_files = (repo_context or {}).get("relevant_files", [])
        if "test" in lower_req and not any("test" in f for f in repo_files):
            # Check repo context to see if pytest or jest exists
            if repo_context and repo_context.get("tests"):
                assumptions.append(
                    Assumption(
                        id="ASM-TEST-FRAMEWORK",
                        statement="Use existing repository test framework for verification.",
                        evidence=f"Discovered test files in repository context: {repo_context['tests'][:2]}",
                        confidence=0.9,
                        consequence_if_wrong="Test runner invocation may fail",
                        validation_step="Run configured test runner",
                    )
                )

        # Check 4: Ambiguous naming / non-blocking conventions
        if any(w in lower_req for w in ["add", "create", "build", "implement"]):
            assumptions.append(
                Assumption(
                    id="ASM-FILE-LOCATION",
                    statement="Follow standard repository directory structure for new file placement.",
                    evidence="Derived from repository project layout",
                    confidence=0.85,
                    consequence_if_wrong="Minor file placement adjustment required",
                    validation_step="Verify imports and directory tree",
                )
            )

        return questions, assumptions
