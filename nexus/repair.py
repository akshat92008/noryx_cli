"""
Autonomous Repair Loop for Nexus CLI.
Integrated with canonical RecoveryController for typed diagnosis, loop prevention,
strategy selection, and budget governance.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nexus.recovery.controller import RecoveryController
from nexus.recovery.records import FailureKind

if TYPE_CHECKING:
    from nexus.agent import Agent

logger = logging.getLogger(__name__)

DEFAULT_MAX_ITERATIONS = 3
DEFAULT_REPAIR_BUDGET_SECONDS = 120


@dataclass
class RepairAttempt:
    """Record of one repair iteration."""

    iteration: int
    failure_kind: FailureKind
    repair_prompt: str
    success: bool
    duration_ms: int = 0
    evidence_ids: list[str] = field(default_factory=list)
    output: str = ""
    context_expansion: dict[str, Any] = field(default_factory=dict)


@dataclass
class RepairReport:
    """Complete repair run outcome."""

    original_prompt: str
    failure_context: str
    attempts: list[RepairAttempt] = field(default_factory=list)
    succeeded: bool = False
    total_duration_ms: int = 0

    def summary(self) -> str:
        if self.succeeded:
            n = len(self.attempts)
            return f"✅ Repair succeeded after {n} iteration{'s' if n != 1 else ''}."
        kinds = {a.failure_kind.value for a in self.attempts}
        return (
            f"❌ Repair exhausted {len(self.attempts)} iterations "
            f"(failure kinds: {', '.join(kinds)})."
        )


class RepairLoop:
    """
    Closed-loop autonomous repair engine powered by RecoveryController.
    """

    _TEMPLATES: dict[FailureKind, str] = {
        FailureKind.SYNTAX: (
            "The previous attempt produced a syntax error:\n\n{failure_context}\n\n"
            "Fix ONLY the syntax error. Do not change any other logic. "
            "Read the file first, identify the exact malformed construct, then apply a minimal edit."
        ),
        FailureKind.IMPORT: (
            "The previous attempt produced an import error:\n\n{failure_context}\n\n"
            "Identify the missing or incorrect import. Check whether the module exists in the "
            "project. If it is a third-party package, verify it is listed in requirements.txt "
            "before installing. Fix only the import — do not rewrite the file."
        ),
        FailureKind.TYPE: (
            "The previous attempt produced a type error:\n\n{failure_context}\n\n"
            "Read the affected function signature and call sites. Apply the minimal type fix "
            "(add annotation, cast, or guard). Run mypy / pyright on that file only to confirm."
        ),
        FailureKind.TEST: (
            "The following tests are failing:\n\n{failure_context}\n\n"
            "Read the test code and the implementation it tests. Identify the assertion that "
            "fails and trace it to its root cause in the production code. Fix the production code "
            "(not the test) unless the test expectation is clearly wrong. Then re-run the tests."
        ),
        FailureKind.RUNTIME: (
            "The previous attempt produced a runtime error:\n\n{failure_context}\n\n"
            "Read the traceback carefully. Identify the exact line that raised the exception. "
            "Add appropriate error handling or fix the root cause. Re-run to verify the fix."
        ),
        FailureKind.DEPENDENCY: (
            "A dependency conflict or missing package was detected:\n\n{failure_context}\n\n"
            "Check requirements.txt and installed packages. Resolve version conflicts or add "
            "the missing dependency. Prefer pinning to a compatible version over upgrading."
        ),
        FailureKind.TIMEOUT: (
            "The previous attempt timed out:\n\n{failure_context}\n\n"
            "Identify the slow operation. Consider: adding a timeout, optimising the hot path, "
            "using async I/O, or caching results. Measure with a quick benchmark after the fix."
        ),
        FailureKind.ENVIRONMENT: (
            "An environment configuration problem was detected:\n\n{failure_context}\n\n"
            "Check environment variables, paths, and platform-specific conditions. "
            "Ensure the fix is portable (Linux/macOS/Windows)."
        ),
        FailureKind.SECURITY: (
            "A security issue was detected:\n\n{failure_context}\n\n"
            "Do not suppress the security check. Fix the underlying vulnerability. "
            "Common fixes: escape user input, parameterise SQL queries, avoid shell=True, "
            "use secrets.token_hex instead of random."
        ),
        FailureKind.UNKNOWN: (
            "The previous attempt produced an unexpected failure:\n\n{failure_context}\n\n"
            "Read the error carefully. Inspect the relevant files. Apply the minimal fix "
            "that addresses the root cause. Run tests to verify."
        ),
    }

    def __init__(
        self,
        agent: "Agent",
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        budget_seconds: float = DEFAULT_REPAIR_BUDGET_SECONDS,
    ) -> None:
        self._agent = agent
        self._max_iterations = max_iterations
        self._budget_seconds = budget_seconds
        self.recovery_controller = getattr(
            agent,
            "recovery_controller",
            RecoveryController(run_id=getattr(agent, "run_id", "run-repair")),
        )

    def attempt(
        self,
        original_prompt: str,
        failure_context: str,
    ) -> bool:
        report = self.run(original_prompt, failure_context)
        return report.succeeded

    def run(
        self,
        original_prompt: str,
        failure_context: str,
    ) -> RepairReport:
        report = RepairReport(
            original_prompt=original_prompt,
            failure_context=failure_context,
        )
        deadline = time.monotonic() + self._budget_seconds
        current_failure = failure_context

        for iteration in range(1, self._max_iterations + 1):
            if time.monotonic() >= deadline:
                logger.info("Repair budget exhausted at iteration %d.", iteration)
                break

            t_start = time.monotonic()

            brain = getattr(self._agent, "engineering_brain", None)
            context_files: list[str] = []
            repository_paths: list[str] = []
            if brain is not None:
                context_bundle = getattr(brain, "context_bundle", None)
                if context_bundle is not None:
                    context_files = [item.path for item in context_bundle.files]
                repository = getattr(brain, "repository", None)
                if repository is not None:
                    repository_paths = list(getattr(repository, "files", {}))

            # Route through canonical RecoveryController
            strategy, diagnosis, terminal = self.recovery_controller.handle_failure(
                current_failure,
                source_component="RepairLoop",
                phase="repair",
                plan_version=1,
                objective=original_prompt,
                context_files=context_files,
                repository_paths=repository_paths,
            )

            if terminal and terminal not in ("CONTINUE", "RETRY"):
                logger.info("RecoveryController returned terminal state: %s", terminal)
                break

            fk_val = diagnosis.primary_failure.kind.value
            try:
                failure_kind = FailureKind(fk_val)
            except ValueError:
                failure_kind = FailureKind.UNKNOWN

            context_expansion: dict[str, Any] = {}
            if brain is not None and getattr(brain, "contract", None) is not None:
                try:
                    context_expansion = brain.expand_context_from_failure(
                        f"repair iteration {iteration}: {strategy.strategy_type.value}",
                        {
                            "failure": current_failure,
                            "recovery_context": self.recovery_controller.last_evidence_context,
                        },
                    )
                except (OSError, TypeError, ValueError) as exc:
                    logger.warning("Evidence-driven context expansion failed: %s", exc)
                    context_expansion = {
                        "expanded": False,
                        "reason": f"context expansion error: {exc}",
                    }

            repair_prompt = self._build_repair_prompt(
                original_prompt=original_prompt,
                failure_context=current_failure,
                failure_kind=failure_kind,
                iteration=iteration,
                strategy_name=strategy.strategy_type.value,
                context_expansion=context_expansion,
            )

            logger.info(
                "Repair iteration %d/%d — strategy=%s kind=%s",
                iteration,
                self._max_iterations,
                strategy.strategy_type.value,
                failure_kind.value,
            )

            evidence_start = len(self._agent.evidence.records())
            try:
                response, events = self._agent._run_hosted_turn(  # noqa: SLF001
                    repair_prompt,
                    analysis={"intent": "fix", "plan_type": "direct"},
                    plan=None,
                    interactive=False,
                    emit_ui=False,
                )
            except (OSError, ValueError) as exc:
                logger.warning("Repair iteration %d raised exception: %s", iteration, exc)
                response = str(exc)
                events = []

            duration_ms = int((time.monotonic() - t_start) * 1000)

            evidence = self._agent.evidence.records()[evidence_start:]
            mutations = [e for e in evidence if e.get("kind") == "file_mutation"]
            checks = []
            verification_output = ""
            if mutations and all(e.get("status") == "verified" for e in mutations):
                verification_report = self._agent._record_verification_report(  # noqa: SLF001
                    self._agent._run_verification_suite()
                )
                verification_output = verification_report.format_report()
                evidence = self._agent.evidence.records()[evidence_start:]
                checks = [e for e in evidence if e.get("kind") == "verification_check"]

            succeeded = bool(mutations) and bool(checks) and all(
                item.get("status") == "verified" for item in [*mutations, *checks]
            )

            attempt = RepairAttempt(
                iteration=iteration,
                failure_kind=failure_kind,
                repair_prompt=repair_prompt[:500],
                success=succeeded,
                duration_ms=duration_ms,
                evidence_ids=[e.get("id", "") for e in [*mutations, *checks]],
                output=(response + "\n\n" + verification_output)[:2000],
                context_expansion=context_expansion,
            )
            report.attempts.append(attempt)

            if succeeded:
                report.succeeded = True
                logger.info("Repair succeeded at iteration %d.", iteration)
                break

            current_failure = (
                verification_output
                or self._extract_failure_from_response(response, events)
                or "Repair attempt made no verified progress."
            )[:8000]

        report.total_duration_ms = sum(a.duration_ms for a in report.attempts)
        if not report.succeeded:
            logger.warning("Repair loop exhausted: %s", report.summary())

        return report

    def _build_repair_prompt(
        self,
        original_prompt: str,
        failure_context: str,
        failure_kind: FailureKind,
        iteration: int,
        strategy_name: str = "",
        context_expansion: dict[str, Any] | None = None,
    ) -> str:
        template = self._TEMPLATES.get(failure_kind, self._TEMPLATES[FailureKind.UNKNOWN])
        body = template.format(
            original_prompt=original_prompt,
            failure_context=failure_context,
            iteration=iteration,
        )
        graph_context = ""
        brain = getattr(self._agent, "engineering_brain", None)
        if brain is not None and getattr(brain, "context_prompt", ""):
            graph_context = str(brain.context_prompt)
            max_context_chars = 180_000
            if len(graph_context) > max_context_chars:
                graph_context = (
                    graph_context[:140_000]
                    + "\n\n[CONTEXT TRUNCATED AT SAFE PROMPT BUDGET]\n\n"
                    + graph_context[-40_000:]
                )
        else:
            repo_graph = getattr(self._agent, "repo_graph", None)
        if not graph_context and repo_graph is not None:
            try:
                graph_context = repo_graph.context_bundle(
                    f"{original_prompt}\n{failure_context}",
                    max_files=10,
                    max_chars=28_000,
                    lines_per_file=90,
                )
            except (OSError, TypeError, ValueError):
                graph_context = ""

        expansion_summary = ""
        if context_expansion:
            expansion_summary = (
                "Evidence-driven context revision:\n"
                f"- Expanded: {bool(context_expansion.get('expanded'))}\n"
                f"- Added files: {', '.join(context_expansion.get('added_paths', [])) or 'none'}\n"
                f"- Failure signals: {', '.join(context_expansion.get('failure_kinds', [])) or 'unclassified'}\n"
                f"- Task profile: {context_expansion.get('task_profile', {}).get('kind', 'unchanged')}\n\n"
            )

        return (
            f"[REPAIR ITERATION {iteration}/{self._max_iterations} | STRATEGY: {strategy_name}]\n\n"
            f"Original task: {original_prompt[:300]}\n\n"
            f"{body}\n\n"
            "Repair protocol:\n"
            "1. Reproduce or inspect the exact failure.\n"
            "2. Identify the smallest responsible code path.\n"
            "3. Read callers, contracts, and the nearest tests before editing.\n"
            "4. Apply a minimal coherent patch.\n"
            "5. Run the narrow failing check first, then applicable project checks.\n"
            "6. Do not claim success while any check is failing.\n\n"
            f"{expansion_summary}"
            f"{graph_context}"
        )

    @staticmethod
    def _extract_failure_from_response(response: str, events: list[dict[str, Any]]) -> str:
        for event in reversed(events):
            if isinstance(event, dict) and event.get("type") == "tool_call":
                if not event.get("success", True):
                    return event.get("detail", response)[:2000]
        return response[:2000]
