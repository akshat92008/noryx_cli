"""Task-intent classification and explainable ranking engine — Sprint 5."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from nexus.intelligence.repository.model import ContextCandidate, RiskLevel, TaskIntent


class TaskIntentClassifier:
    """Classifies task description into structured TaskIntent."""

    @staticmethod
    def classify(task_description: str) -> TaskIntent:
        desc = task_description.lower()
        if any(w in desc for w in ("fix", "bug", "crash", "error", "fail", "broken", "issue", "exception")):
            if "test" in desc:
                return TaskIntent.TEST_REPAIR
            return TaskIntent.BUG_REPAIR
        elif any(w in desc for w in ("add", "build", "create", "implement", "feature", "new")):
            if "test" in desc:
                return TaskIntent.TEST_CREATION
            return TaskIntent.FEATURE_IMPLEMENTATION
        elif any(w in desc for w in ("refactor", "consolidate", "cleanup", "reorganize", "decouple")):
            return TaskIntent.REFACTOR
        elif any(w in desc for w in ("migrate", "migration", "upgrade version")):
            return TaskIntent.MIGRATION
        elif any(w in desc for w in ("test", "pytest", "spec")):
            return TaskIntent.TEST_CREATION
        elif any(w in desc for w in ("security", "auth", "permission", "secret", "token", "vulnerability")):
            return TaskIntent.SECURITY_FIX
        elif any(w in desc for w in ("config", "setting", "setup", "env")):
            return TaskIntent.CONFIGURATION_CHANGE
        elif any(w in desc for w in ("perf", "performance", "slow", "optimize", "speed")):
            return TaskIntent.PERFORMANCE_OPTIMIZATION
        elif any(w in desc for w in ("doc", "readme", "explain", "how to", "understand", "audit")):
            return TaskIntent.EXPLANATION
        return TaskIntent.GENERAL


class ExplainableContextRanker:
    """Ranks context candidates deterministically based on task intent and graph signals."""

    def rank_candidates(
        self,
        task_description: str,
        files: dict[str, Any],  # path -> RepositoryFile
        changed_git_files: list[str],
        explicit_user_files: list[str] | None = None,
        failing_stack_files: list[str] | None = None,
        limit: int = 40,
    ) -> list[ContextCandidate]:
        intent = TaskIntentClassifier.classify(task_description)
        terms = {
            t.lower() for t in re.findall(r"[A-Za-z_][A-Za-z0-9_-]{2,}", task_description)
            if t.lower() not in {"add", "build", "create", "fix", "make", "implement", "with", "from", "this", "that", "the", "and"}
        }

        explicit_set = set(explicit_user_files or [])
        stack_set = set(failing_stack_files or [])
        changed_set = set(changed_git_files)

        candidates: list[ContextCandidate] = []

        for rel_path, repo_file in files.items():
            score = 0.0
            reasons: list[str] = []
            source_signal = "text_search"

            # 1. Explicit user reference (Highest Priority)
            if rel_path in explicit_set:
                score += 50.0
                reasons.append("Explicitly specified by user")
                source_signal = "user_explicit"

            # 2. Failing stack trace match
            if rel_path in stack_set:
                score += 40.0
                reasons.append("Present in failing stack trace / test output")
                source_signal = "stack_trace"

            # 3. Path / Name matching
            path_lower = rel_path.lower()
            desc_lower = task_description.lower()

            # Exact path match or directory scope match
            if rel_path.lower() in desc_lower:
                score += 35.0
                reasons.append("Exact file path mentioned in task")

            for term in terms:
                if term in path_lower:
                    score += 8.0
                    reasons.append(f"Filename matches task term: '{term}'")

            # Monorepo directory package scoping
            if "packages/" in desc_lower:
                for pkg_part in desc_lower.split():
                    if pkg_part.startswith("packages/"):
                        pkg_dir = "/".join(pkg_part.split("/")[:2])  # e.g. packages/pkg_a
                        if pkg_dir in path_lower:
                            score += 20.0
                            reasons.append(f"Belongs to target monorepo package: {pkg_dir}")
                        elif path_lower.startswith("packages/"):
                            score *= 0.1  # Penalize files in unrelated monorepo packages

            # 4. Symbol matching
            matched_symbols = []
            for symbol in repo_file.symbols:
                s_name = symbol.name.lower()
                for term in terms:
                    if term == s_name or term == symbol.qualified_name.lower():
                        matched_symbols.append(symbol.name)
                        score += 15.0
                    elif term in s_name:
                        matched_symbols.append(symbol.name)
                        score += 6.0

            if matched_symbols:
                reasons.append(f"Contains matching symbols: {', '.join(list(dict.fromkeys(matched_symbols))[:4])}")
                if source_signal == "text_search":
                    source_signal = "exact_symbol"

            # 5. Route and Model matching
            for route in repo_file.routes:
                if any(t in route.lower() for t in terms):
                    score += 12.0
                    reasons.append(f"Matches API route: {route}")
            for model_name in repo_file.database_models:
                if any(t in model_name.lower() for t in terms):
                    score += 12.0
                    reasons.append(f"Matches DB model: {model_name}")

            # 6. Task Intent Specific Adjustments
            if intent in (TaskIntent.BUG_REPAIR, TaskIntent.TEST_REPAIR):
                if repo_file.is_test and any(t in task_description.lower() for t in ("test", "bug", "fail", "regression")):
                    score += 15.0
                    reasons.append("Test file prioritized for bug repair task")
            elif intent == TaskIntent.CONFIGURATION_CHANGE:
                if repo_file.config_file:
                    score += 20.0
                    reasons.append("Configuration file prioritized for config task")
            elif intent == TaskIntent.SECURITY_FIX:
                if repo_file.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                    score += 25.0
                    reasons.append("Security-sensitive component prioritized for security fix")

            # 7. Git Working Tree Changes
            if rel_path in changed_set:
                score += 5.0
                reasons.append("Recently modified in Git working tree")

            # 8. Penalties for Generated or Vendored files
            if repo_file.generated:
                score *= 0.2
                reasons.append("Penalized (generated file)")
            if repo_file.vendored:
                score *= 0.1
                reasons.append("Penalized (vendored directory)")

            if score > 0:
                est_tokens = max(50, repo_file.size_bytes // 4)
                candidates.append(
                    ContextCandidate(
                        path=rel_path,
                        source_signal=source_signal,
                        relationship=f"Matches task intent ({intent.value})",
                        confidence=min(1.0, score / 50.0),
                        estimated_tokens=est_tokens,
                        risk=repo_file.risk_level,
                        reasons=list(dict.fromkeys(reasons)),
                        score=score,
                    )
                )

        candidates.sort(key=lambda c: (-c.score, c.path))
        return candidates[: max(1, limit)]
