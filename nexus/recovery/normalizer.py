"""
Failure Normalization Layer for Nexus CLI.
Converts raw, provider-specific, tool-specific or runner-specific outputs into canonical FailureRecords.
"""

from __future__ import annotations

import re

from nexus.recovery.records import (
    EvidenceReference,
    FailureCategory,
    FailureKind,
    FailureRecord,
    FailureSeverity,
)


class FailureNormalizer:
    """Canonical normalization engine for tool, runner, and system failures."""

    @classmethod
    def normalize(
        self,
        raw_output: str | FailureRecord,
        *,
        source_component: str = "executor",
        phase: str = "execution",
        run_id: str = "run-001",
        plan_version: int = 1,
        attempt_number: int = 1,
        command: str = "",
        exit_code: int | None = None,
        metadata: dict | None = None,
    ) -> FailureRecord:
        if isinstance(raw_output, FailureRecord):
            return raw_output
        output = str(raw_output or "")
        meta = metadata or {}
        file_paths = meta.get("file_paths") or self._extract_file_paths(output)
        line_numbers = meta.get("line_numbers") or self._extract_line_numbers(output)
        symbols = meta.get("symbols") or self._extract_symbols(output)
        failing_tests = meta.get("failing_tests") or self._extract_failing_tests(output)

        category, kind, severity, retryable, user_req = self._classify_output(
            output, command=command, exit_code=exit_code
        )

        summary = self._summarize_failure(output, kind, failing_tests, file_paths)
        evidence_ref = EvidenceReference(
            evidence_id=f"ev-{hash(output[:200]) & 0xFFFFFFFF:08x}",
            kind="raw_output",
            source=source_component,
            summary=summary[:200],
        )

        return FailureRecord(
            failure_id=f"fail-{hash(output[:100] + str(attempt_number)) & 0xFFFFFFFF:08x}",
            run_id=run_id,
            category=category,
            kind=kind,
            source_component=source_component,
            phase=phase,
            summary=summary,
            evidence=[evidence_ref],
            repository_state="",
            plan_version=plan_version,
            attempt_number=attempt_number,
            retryable=retryable,
            severity=severity,
            user_action_required=user_req,
            raw_output=output,
            file_paths=file_paths,
            line_numbers=line_numbers,
            symbols=symbols,
            failing_tests=failing_tests,
            command=command,
            exit_code=exit_code,
            metadata=meta,
        )

    @classmethod
    def _classify_output(
        cls, output: str, command: str = "", exit_code: int | None = None
    ) -> tuple[FailureCategory, FailureKind, FailureSeverity, bool, bool]:
        norm = re.sub(r"\s+", " ", output.lower())

        # Environment & Resource
        if "command not found" in norm or "executable_not_found" in norm:
            return (
                FailureCategory.TOOL_EXECUTION,
                FailureKind.EXECUTABLE_NOT_FOUND,
                FailureSeverity.HIGH,
                False,
                True,
            )
        if "permission denied" in norm or "EACCES" in norm:
            return (
                FailureCategory.TOOL_EXECUTION,
                FailureKind.PERMISSION_DENIED,
                FailureSeverity.HIGH,
                False,
                True,
            )
        if "policy blocked" in norm or "blocked by policy" in norm:
            return (
                FailureCategory.TOOL_EXECUTION,
                FailureKind.POLICY_BLOCKED,
                FailureSeverity.HIGH,
                False,
                True,
            )
        if "timed out" in norm or "timeout" in norm:
            return (
                FailureCategory.TOOL_EXECUTION,
                FailureKind.COMMAND_TIMEOUT,
                FailureSeverity.MEDIUM,
                True,
                False,
            )
        if "no matching distribution" in norm or "pip install" in norm and "failed" in norm or "modulenotfounderror" in norm or "no module named" in norm:
            return (
                FailureCategory.ENVIRONMENT,
                FailureKind.DEPENDENCY_MISSING,
                FailureSeverity.MEDIUM,
                True,
                True,
            )
        if "budget exhausted" in norm:
            return (
                FailureCategory.RESOURCE,
                FailureKind.BUDGET_EXHAUSTED,
                FailureSeverity.HIGH,
                False,
                True,
            )

        # Mutation & Patch
        if "patch conflict" in norm or "hunk failed" in norm or "hunk #" in norm or "patch failed" in norm or "corrupt patch" in norm:
            return (
                FailureCategory.MUTATION,
                FailureKind.PATCH_CONFLICT,
                FailureSeverity.MEDIUM,
                True,
                False,
            )
        if "protected path" in norm or "read-only" in norm:
            return (
                FailureCategory.MUTATION,
                FailureKind.PROTECTED_PATH,
                FailureSeverity.HIGH,
                False,
                True,
            )

        # Model & Parsing
        if "invalid json" in norm or "jsondecodeerror" in norm or "parse error" in norm:
            return (
                FailureCategory.MODEL,
                FailureKind.INVALID_STRUCTURED_OUTPUT,
                FailureSeverity.MEDIUM,
                True,
                False,
            )

        # Verification / Tests / Build / Types
        if "syntaxerror" in norm or "syntax error" in norm:
            return (
                FailureCategory.VERIFICATION,
                FailureKind.BUILD_FAILED,
                FailureSeverity.HIGH,
                True,
                False,
            )
        if "typeerror" in norm or "type error" in norm or "mypy" in norm or "ts2322" in norm:
            return (
                FailureCategory.VERIFICATION,
                FailureKind.TYPE_CHECK_FAILED,
                FailureSeverity.MEDIUM,
                True,
                False,
            )
        if "flake8" in norm or "ruff" in norm or "eslint" in norm or "lint" in norm:
            return (
                FailureCategory.VERIFICATION,
                FailureKind.LINT_FAILED,
                FailureSeverity.LOW,
                True,
                False,
            )
        if (
            "assertionerror" in norm
            or "failed test" in norm
            or "assert " in norm
            or "FAILED " in output
        ):
            return (
                FailureCategory.VERIFICATION,
                FailureKind.TARGETED_TEST_FAILED,
                FailureSeverity.MEDIUM,
                True,
                False,
            )
        if "no tests collected" in norm or "collected 0 items" in norm:
            return (
                FailureCategory.VERIFICATION,
                FailureKind.NO_TESTS_COLLECTED,
                FailureSeverity.LOW,
                True,
                False,
            )

        # Default tool command failure or unknown
        if exit_code is not None and exit_code != 0:
            return (
                FailureCategory.TOOL_EXECUTION,
                FailureKind.COMMAND_FAILED,
                FailureSeverity.MEDIUM,
                True,
                False,
            )

        return FailureCategory.TOOL_EXECUTION, FailureKind.UNKNOWN, FailureSeverity.LOW, True, False

    @classmethod
    def _extract_file_paths(cls, output: str) -> list[str]:
        # Match common file path formats in py/js stack traces
        matches = re.findall(r'(?:File "([^"]+)"|([a-zA-Z0-9_\-\./]+\.(?:py|js|ts|json|md|txt)))', output)
        paths = []
        for m in matches:
            p = m[0] or m[1]
            if p and p not in paths:
                paths.append(p)
        return paths[:10]

    @classmethod
    def _extract_line_numbers(cls, output: str) -> list[int]:
        matches = re.findall(r'line (\d+)|:(\d+):', output)
        lines = []
        for m in matches:
            num = int(m[0] or m[1])
            if num not in lines:
                lines.append(num)
        return lines[:10]

    @classmethod
    def _extract_symbols(cls, output: str) -> list[str]:
        matches = re.findall(r"(?:in ([a-zA-Z0-9_]+)|NameError: name '([^']+)'|AttributeError: '[^']+' object has no attribute '([^']+)')", output)
        symbols = []
        for m in matches:
            sym = m[0] or m[1] or m[2]
            if sym and sym not in symbols:
                symbols.append(sym)
        return symbols[:10]

    @classmethod
    def _extract_failing_tests(cls, output: str) -> list[str]:
        # Pytest FAILED node id match
        matches = re.findall(r"FAILED\s+([^\s]+)", output)
        tests = []
        for m in matches:
            if m not in tests:
                tests.append(m)
        return tests[:10]

    @classmethod
    def _summarize_failure(
        cls, output: str, kind: Any, tests: list[str], files: list[str]
    ) -> str:
        k_val = str(kind.value if hasattr(kind, "value") else kind).upper()
        if tests:
            return f"{k_val}: Test failed: {tests[0]}"
        if files:
            return f"{k_val}: Error in {files[0]}"
        first_line = output.strip().splitlines()[0] if output.strip() else "Unknown failure"
        return f"{k_val}: {first_line[:120]}"
