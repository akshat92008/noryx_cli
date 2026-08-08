"""
Baseline-Aware Failure Analysis for Nexus CLI.
Compares pre-run baseline failures, pre-mutation state, and post-mutation evidence.
"""

from __future__ import annotations

from enum import Enum


class FailureRelation(str, Enum):
    INHERITED = "inherited"
    NEW_REGRESSION = "new_regression"
    PERSISTENT_TARGET_FAILURE = "persistent_target_failure"
    RESOLVED = "resolved"
    FLAKY = "flaky"
    ENVIRONMENTAL = "environmental"
    UNKNOWN = "unknown"


class BaselineAnalyzer:
    """Categorizes test and system failures against repository baseline state."""

    @classmethod
    def analyze(
        cls,
        failing_tests: list[str],
        baseline_failures: list[str] | set[str],
        previous_attempt_failures: list[str] | set[str] | None = None,
        target_tests: list[str] | set[str] | None = None,
        is_environment_error: bool = False,
    ) -> dict[str, FailureRelation]:
        base_set = set(baseline_failures)
        prev_set = set(previous_attempt_failures or [])
        target_set = set(target_tests or [])

        results: dict[str, FailureRelation] = {}
        for test in failing_tests:
            if is_environment_error:
                results[test] = FailureRelation.ENVIRONMENTAL
            elif test in base_set and test not in target_set:
                results[test] = FailureRelation.INHERITED
            elif test in target_set:
                results[test] = FailureRelation.PERSISTENT_TARGET_FAILURE
            elif test not in base_set:
                results[test] = FailureRelation.NEW_REGRESSION
            else:
                results[test] = FailureRelation.UNKNOWN

        return results

    @classmethod
    def prioritize_failures(
        cls, relations: dict[str, FailureRelation]
    ) -> list[tuple[str, FailureRelation]]:
        """
        Prioritizes:
        1. NEW_REGRESSION
        2. PERSISTENT_TARGET_FAILURE
        3. ENVIRONMENTAL
        4. INHERITED
        """
        priority_map = {
            FailureRelation.NEW_REGRESSION: 1,
            FailureRelation.PERSISTENT_TARGET_FAILURE: 2,
            FailureRelation.ENVIRONMENTAL: 3,
            FailureRelation.INHERITED: 4,
            FailureRelation.FLAKY: 5,
            FailureRelation.UNKNOWN: 6,
            FailureRelation.RESOLVED: 7,
        }
        return sorted(relations.items(), key=lambda item: priority_map.get(item[1], 99))
