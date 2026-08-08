"""
Specialized Diagnosers for Nexus CLI Recovery Subsystem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class PatchDiagnosisResult:
    is_valid: bool
    is_empty: bool
    is_placeholder: bool
    is_broad_rewrite: bool
    syntax_error: str
    summary: str


class PatchQualityDiagnoser:
    """Diagnoses patch quality before or after applying code mutations."""

    @classmethod
    def analyze_patch(cls, patch_content: str, target_file: str = "") -> PatchDiagnosisResult:
        if not patch_content or not patch_content.strip():
            return PatchDiagnosisResult(
                is_valid=False,
                is_empty=True,
                is_placeholder=False,
                is_broad_rewrite=False,
                syntax_error="",
                summary="Patch is empty or whitespace-only.",
            )

        # Check for placeholder markers
        placeholders = ["TODO", "# Implement here", "PASS", "..."]
        has_placeholder = any(p in patch_content for p in placeholders)

        # Check Python syntax if python file
        syntax_err = ""
        if target_file.endswith(".py"):
            try:
                compile(patch_content, target_file or "<string>", "exec")
            except SyntaxError as e:
                syntax_err = f"Line {e.lineno}: {e.msg}"

        lines = patch_content.splitlines()
        is_broad = len(lines) > 400

        valid = not syntax_err and not (cls._is_pure_no_op(patch_content))

        summary = "Patch is valid."
        if syntax_err:
            summary = f"Patch contains syntax error: {syntax_err}"
        elif has_placeholder:
            summary = "Patch contains unimplemented placeholder code."

        return PatchDiagnosisResult(
            is_valid=valid,
            is_empty=False,
            is_placeholder=has_placeholder,
            is_broad_rewrite=is_broad,
            syntax_error=syntax_err,
            summary=summary,
        )

    @classmethod
    def _is_pure_no_op(cls, patch: str) -> bool:
        # Check if patch only contains comments or whitespace changes
        non_comment_lines = [
            l.strip()
            for l in patch.splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]
        return len(non_comment_lines) == 0


class TestFailureDiagnoser:
    """Diagnoses test failure causes (assertion vs setup vs fixture vs collection)."""

    @classmethod
    def analyze_test_failure(cls, raw_output: str) -> str:
        if "ERROR collecting" in raw_output or "CollectionError" in raw_output:
            return "Test collection failed due to syntax/import error in test files."
        if "fixture" in raw_output.lower() and "not found" in raw_output.lower():
            return "Test setup failed due to missing or misconfigured fixture."
        if "AssertionError" in raw_output:
            return "Assertion failed: expected value did not match actual return value."
        return "Target test failed during execution."


class BuildLintTypeDiagnoser:
    """Diagnoses build, lint, and type check failure details."""

    @classmethod
    def analyze_type_failure(cls, output: str) -> str:
        match = re.search(r'error:\s*(.*)', output)
        return match.group(1) if match else "Type signature mismatch detected."


class EnvironmentDiagnoser:
    """Identifies environmental blockers without suggesting code modifications."""

    @classmethod
    def is_environment_issue(cls, output: str) -> bool:
        norm = output.lower()
        env_keywords = [
            "command not found",
            "not installed",
            "connection refused",
            "no matching distribution",
            "eaccess",
            "permission denied",
            "quota exceeded",
            "rate limit",
        ]
        return any(k in norm for k in env_keywords)
