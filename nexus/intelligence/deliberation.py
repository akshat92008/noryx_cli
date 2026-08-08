"""Hypothesis-driven engineering deliberation contracts.

This module does not pretend to make a weak model intelligent.  It creates a
strict reasoning protocol that requires falsifiable hypotheses, invariants,
negative evidence, and completion confidence before mutation or success.
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Iterable


class DeliberationStage(str, Enum):
    OBSERVE = "observe"
    HYPOTHESIZE = "hypothesize"
    FALSIFY = "falsify"
    PLAN = "plan"
    MUTATE = "mutate"
    VERIFY = "verify"
    CRITIQUE = "critique"


@dataclass(frozen=True)
class EngineeringHypothesis:
    hypothesis_id: str
    statement: str
    predicted_evidence: tuple[str, ...]
    falsifying_evidence: tuple[str, ...]
    cheap_checks: tuple[str, ...]
    confidence: float
    category: str = "root_cause"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeliberationContract:
    schema_version: str
    objective: str
    task_type: str
    risk_level: str
    context_tree_hash: str
    stages: list[str]
    hypotheses: list[EngineeringHypothesis]
    invariants: list[str]
    evidence_requirements: list[str]
    mutation_preconditions: list[str]
    stop_conditions: list[str]
    confidence_floor_for_completion: float
    decisive_files: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["hypotheses"] = [item.to_dict() for item in self.hypotheses]
        return payload

    def to_prompt(self) -> str:
        lines = [
            "[ENGINEERING DELIBERATION CONTRACT]",
            f"Objective: {self.objective}",
            f"Task type: {self.task_type}; risk: {self.risk_level}",
            f"Repository tree: {self.context_tree_hash or 'unknown'}",
            "Reasoning stages: " + " -> ".join(self.stages),
            "Do not mutate until the observation and falsification checks below are complete.",
        ]
        if self.decisive_files:
            lines.append("Decisive files: " + ", ".join(self.decisive_files))
        if self.symbols:
            lines.append("Decisive symbols: " + ", ".join(self.symbols))
        lines.append("Hypotheses:")
        for item in self.hypotheses:
            lines.append(f"- {item.hypothesis_id}: {item.statement} (prior={item.confidence:.2f})")
            lines.append("  Predicts: " + "; ".join(item.predicted_evidence))
            lines.append("  Falsified by: " + "; ".join(item.falsifying_evidence))
            lines.append("  Cheapest checks: " + "; ".join(item.cheap_checks))
        lines.append("Invariants:")
        lines.extend(f"- {item}" for item in self.invariants)
        lines.append("Completion evidence:")
        lines.extend(f"- {item}" for item in self.evidence_requirements)
        lines.append("Stop/replan conditions:")
        lines.extend(f"- {item}" for item in self.stop_conditions)
        lines.append(
            f"Minimum completion confidence: {self.confidence_floor_for_completion:.2f}. "
            "A model assertion is not evidence."
        )
        return "\n".join(lines)


class DeliberationCompiler:
    """Compile deterministic, risk-aware reasoning obligations."""

    @classmethod
    def compile(
        cls,
        objective: str,
        *,
        task_type: str,
        risk_level: str,
        context_tree_hash: str = "",
        decisive_files: Iterable[str] = (),
        related_tests: Iterable[str] = (),
        symbols: Iterable[str] = (),
    ) -> DeliberationContract:
        text = objective.lower()
        files = list(dict.fromkeys(str(item) for item in decisive_files if str(item)))
        tests = list(dict.fromkeys(str(item) for item in related_tests if str(item)))
        symbol_list = list(dict.fromkeys(str(item) for item in symbols if str(item)))
        hypotheses = cls._hypotheses(
            objective,
            task_type=task_type,
            files=files,
            tests=tests,
            symbols=symbol_list,
        )
        invariants = [
            "Only repository-observed files and evidence-backed dependencies may be changed.",
            "Public API, serialization schema, and documented behavior remain stable unless explicitly requested.",
            "The patch must address the confirmed cause rather than only suppressing the symptom.",
            "All changed behavior requires a targeted regression check through the canonical runtime path.",
            "Fail closed when context, tool status, repository revision, or verification evidence is ambiguous.",
        ]
        if any(term in text for term in ("race", "concurr", "atomic", "lock", "thread", "async")):
            invariants.append(
                "Concurrency atomicity and ordering guarantees must be preserved under repeated execution."
            )
        if any(term in text for term in ("auth", "security", "permission", "credential", "token")):
            invariants.append(
                "Authentication, authorization, confidentiality, and least-privilege boundaries cannot weaken."
            )
        if any(term in text for term in ("migration", "schema", "database")):
            invariants.append(
                "Migration is reversible or explicitly one-way, preserves existing data, and has rollback evidence."
            )
        if "without changing" in text or "do not" in text or "don't" in text:
            invariants.append("Every explicit non-goal must be checked against the final diff.")

        evidence_requirements = [
            "A pre-mutation reproduction, baseline, or deterministic observation of current behavior.",
            "Evidence selecting one hypothesis and rejecting plausible alternatives.",
            "A content-addressed diff limited to the authorized completion contract.",
            "Targeted verification for changed behavior and a relevant regression gate.",
            "A final diff critique mapping each change to the objective and an acceptance criterion.",
        ]
        if tests:
            evidence_requirements.append(
                "Verification must execute the discovered related tests: " + ", ".join(tests[:8])
            )
        if risk_level in {"high", "critical"}:
            evidence_requirements.extend(
                [
                    "An independent deterministic security/architecture check appropriate to the risk.",
                    "No success claim below the configured high-risk confidence floor.",
                ]
            )

        mutation_preconditions = [
            "Repository tree hash still matches the planning revision.",
            "Every blocking decisive file has been inspected.",
            "At least one falsification check has been evaluated for the leading hypothesis.",
            "The planned tools exist in the live tool registry.",
        ]
        stop_conditions = [
            "Repository revision changes after planning.",
            "The leading hypothesis is contradicted without a replacement hypothesis.",
            "Required context remains missing after bounded expansion.",
            "A repeated failure occurs without new evidence, plan revision, context expansion, or model change.",
            "Rollback, sandbox, verification, or structured status cannot be trusted.",
        ]
        confidence_floor = 0.90 if risk_level in {"high", "critical"} else 0.78
        return DeliberationContract(
            schema_version="nexus.deliberation.v1",
            objective=objective,
            task_type=task_type,
            risk_level=risk_level,
            context_tree_hash=context_tree_hash,
            stages=[item.value for item in DeliberationStage],
            hypotheses=hypotheses,
            invariants=list(dict.fromkeys(invariants)),
            evidence_requirements=list(dict.fromkeys(evidence_requirements)),
            mutation_preconditions=mutation_preconditions,
            stop_conditions=stop_conditions,
            confidence_floor_for_completion=confidence_floor,
            decisive_files=files,
            related_tests=tests,
            symbols=symbol_list,
        )

    @classmethod
    def _hypotheses(
        cls,
        objective: str,
        *,
        task_type: str,
        files: list[str],
        tests: list[str],
        symbols: list[str],
    ) -> list[EngineeringHypothesis]:
        text = objective.lower()
        target = symbols[0] if symbols else (files[0] if files else "the failing path")
        observed = files[0] if files else "the primary implementation path"
        test_hint = tests[0] if tests else "a minimal deterministic reproduction"
        candidates: list[
            tuple[str, str, tuple[str, ...], tuple[str, ...], tuple[str, ...], float]
        ] = []
        if any(term in text for term in ("race", "concurr", "thread", "atomic", "lock")):
            candidates.extend(
                [
                    (
                        "atomicity",
                        f"{target} performs a read/decision/write sequence without one atomic boundary.",
                        (
                            "interleaving permits duplicate or stale state",
                            "shared state changes between decision and commit",
                        ),
                        ("the operation is already protected by one effective lock/transaction",),
                        (
                            f"inspect {observed} and direct callers",
                            f"stress {test_hint} with repeated interleavings",
                        ),
                        0.72,
                    ),
                    (
                        "ownership",
                        "Multiple code paths independently own the same state transition.",
                        (
                            "two callers mutate the same resource",
                            "duplicate transition logic exists",
                        ),
                        ("all mutations delegate to one authoritative owner",),
                        ("search reverse imports and callers", "compare transition preconditions"),
                        0.58,
                    ),
                ]
            )
        if any(term in text for term in ("auth", "permission", "token", "session")):
            candidates.append(
                (
                    "contract",
                    "Authentication state or authorization checks are evaluated against stale or incomplete context.",
                    (
                        "a caller bypasses canonical validation",
                        "state validation and use occur at different revisions",
                    ),
                    ("every caller uses the same current-state validation immediately before use",),
                    (
                        "trace entrypoint-to-storage data flow",
                        "test denied, expired, and concurrent cases",
                    ),
                    0.64,
                )
            )
        if task_type in {"bug_repair", "security_remediation", "refactor"}:
            candidates.extend(
                [
                    (
                        "boundary",
                        "The visible failure originates at a dependency or interface boundary rather than the reported line.",
                        (
                            "caller/callee assumptions disagree",
                            "input, return, exception, or lifecycle contract differs",
                        ),
                        (
                            "all boundary contracts are consistent and the defect reproduces locally",
                        ),
                        ("inspect direct imports, callers, implementers, and tests",),
                        0.55,
                    ),
                    (
                        "baseline",
                        "The failure is inherited or environment-specific and not caused by the targeted implementation.",
                        (
                            "the baseline fails identically",
                            "failure varies with runtime/configuration",
                        ),
                        (
                            "a clean baseline passes and the target mutation deterministically controls the outcome",
                        ),
                        ("capture pre-mutation baseline", "compare normalized failure signatures"),
                        0.35,
                    ),
                ]
            )
        else:
            candidates.extend(
                [
                    (
                        "integration",
                        "The requested capability requires coordinated changes across implementation, callers, tests, and packaging.",
                        ("multiple repository relationships are impacted",),
                        ("one isolated change satisfies all executable acceptance criteria",),
                        ("build an impact graph", "inspect package/config entrypoints"),
                        0.70,
                    ),
                    (
                        "compatibility",
                        "An existing public or persisted contract constrains the implementation shape.",
                        ("external callers or fixtures depend on current behavior",),
                        ("the code is private and has no persisted consumers",),
                        ("search public exports, schemas, fixtures, and migrations",),
                        0.55,
                    ),
                ]
            )
        if len(candidates) < 3:
            candidates.append(
                (
                    "test_gap",
                    "Existing tests do not exercise the canonical runtime route containing the defect.",
                    ("unit paths pass while end-to-end behavior fails",),
                    (
                        "an existing test invokes the same production boundary and reproduces the issue",
                    ),
                    ("map tests to production imports and entrypoints",),
                    0.50,
                )
            )
        result: list[EngineeringHypothesis] = []
        for index, (category, statement, predicts, falsifies, checks, confidence) in enumerate(
            candidates[:5], 1
        ):
            digest = hashlib.sha256(f"{category}:{statement}".encode()).hexdigest()[:8]
            result.append(
                EngineeringHypothesis(
                    hypothesis_id=f"H{index}-{digest}",
                    statement=statement,
                    predicted_evidence=tuple(predicts),
                    falsifying_evidence=tuple(falsifies),
                    cheap_checks=tuple(checks),
                    confidence=confidence,
                    category=category,
                )
            )
        return result
