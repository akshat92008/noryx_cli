"""Completion contracts for difficult, previously unseen multi-file tasks.

The existing EngineeringChangeSet models *planned edits*.  This module models
what must be inspected, changed, and verified before the run may be considered
complete.  It deliberately separates obligations from model prose.
"""
from __future__ import annotations

import hashlib
import posixpath
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any, Iterable

from nexus.intelligence.repository.engine import RepositoryIntelligence


@dataclass(frozen=True)
class FileObligation:
    path: str
    obligation: str  # inspect, change, verify, preserve
    reason: str
    confidence: float = 1.0
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CompletionAssessment:
    complete: bool
    missing_inspection: tuple[str, ...] = ()
    missing_changes: tuple[str, ...] = ()
    missing_verification: tuple[str, ...] = ()
    unexpected_changes: tuple[str, ...] = ()
    preserved_file_violations: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    enforcement_mode: str = "advisory"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MultiFileCompletionContract:
    schema_version: str
    objective: str
    repository_tree_hash: str
    obligations: list[FileObligation] = field(default_factory=list)
    allowed_change_roots: list[str] = field(default_factory=list)
    required_change_files: list[str] = field(default_factory=list)
    required_verification_files: list[str] = field(default_factory=list)
    invariants: list[str] = field(default_factory=list)
    minimum_changed_files: int = 0
    enforcement_mode: str = "advisory"
    task_type: str = ""
    risk_level: str = "medium"
    contract_id: str = ""

    def __post_init__(self) -> None:
        if not self.contract_id:
            canonical = "|".join(
                [self.objective, self.repository_tree_hash, *(item.path + item.obligation for item in self.obligations)]
            )
            self.contract_id = hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["obligations"] = [item.to_dict() for item in self.obligations]
        return payload

    @property
    def inspect_files(self) -> set[str]:
        return {item.path for item in self.obligations if item.obligation == "inspect" and item.blocking}

    @property
    def preserve_files(self) -> set[str]:
        return {item.path for item in self.obligations if item.obligation == "preserve" and item.blocking}

    @property
    def hard_enforcement(self) -> bool:
        return self.enforcement_mode == "hard"

    def assess(
        self,
        *,
        inspected_files: Iterable[str],
        changed_files: Iterable[str],
        verified_files: Iterable[str],
    ) -> CompletionAssessment:
        inspected = {self._normalize(item) for item in inspected_files if str(item).strip()}
        changed = {self._normalize(item) for item in changed_files if str(item).strip()}
        verified = {self._normalize(item) for item in verified_files if str(item).strip()}
        required_changes = {self._normalize(item) for item in self.required_change_files}
        required_verification = {self._normalize(item) for item in self.required_verification_files}
        missing_inspection = sorted(self.inspect_files - inspected)
        missing_changes = sorted(required_changes - changed)
        missing_verification = sorted(required_verification - verified)
        unexpected = sorted(path for path in changed if not self._allowed(path))
        preserved = sorted(changed.intersection(self.preserve_files))
        changed_count_missing = self.minimum_changed_files > len(changed)
        complete = not any(
            (
                missing_inspection,
                missing_changes,
                missing_verification,
                unexpected,
                preserved,
                changed_count_missing,
            )
        )
        if not self.hard_enforcement:
            # Advisory contracts may omit low-confidence inspection obligations,
            # but never permit unexpected paths or missing explicit verification.
            complete = not any((missing_changes, missing_verification, unexpected, preserved, changed_count_missing))
        return CompletionAssessment(
            complete=complete,
            missing_inspection=tuple(missing_inspection),
            missing_changes=tuple(missing_changes),
            missing_verification=tuple(missing_verification),
            unexpected_changes=tuple(unexpected),
            preserved_file_violations=tuple(preserved),
            changed_files=tuple(sorted(changed)),
            enforcement_mode=self.enforcement_mode,
        )

    def _allowed(self, path: str) -> bool:
        if path in self.required_change_files:
            return True
        return any(path == root or path.startswith(root.rstrip("/") + "/") for root in self.allowed_change_roots)

    @staticmethod
    def _normalize(path: str) -> str:
        raw = str(path).replace("\\", "/").strip()
        normalized = posixpath.normpath(raw)
        if normalized in {"", ".", ".."} or normalized.startswith("../") or normalized.startswith("/"):
            raise ValueError(f"Invalid repository-relative path: {path!r}")
        return str(PurePosixPath(normalized))


class MultiFileOrchestrator:
    """Derive an auditable completion contract from repository evidence."""

    @classmethod
    def derive(
        cls,
        objective: str,
        *,
        repository: RepositoryIntelligence,
        decisive_files: Iterable[str] = (),
        callers: Iterable[str] = (),
        related_tests: Iterable[str] = (),
        explicit_files: Iterable[str] = (),
        non_goals: Iterable[str] = (),
        task_type: str = "",
        risk_level: str = "medium",
    ) -> MultiFileCompletionContract:
        explicit = cls._dedupe(explicit_files)
        decisive = cls._dedupe(decisive_files)
        caller_paths = cls._dedupe(callers)
        tests = cls._dedupe(related_tests)
        obligations: list[FileObligation] = []

        for path in explicit:
            obligations.append(FileObligation(path, "inspect", "User explicitly named this file.", 1.0, True))
        for path in decisive:
            obligations.append(FileObligation(path, "inspect", "Repository context ranked this file as decisive.", 0.90, True))
        normalized_task = str(task_type).lower()
        lowered = objective.lower()
        repository_wide = any(term in lowered for term in (
            "repository-wide", "repository wide", "all callers", "every caller",
            "across the repo", "across the repository", "public api",
            "signature change", "breaking change",
        ))
        coordinated_caller_change = repository_wide or normalized_task in {
            "migration", "refactor", "repository_wide_api_change", "framework_migration",
        }
        for path in caller_paths:
            obligation = "change" if coordinated_caller_change else "inspect"
            obligations.append(FileObligation(
                path, obligation,
                "Direct caller or reverse dependency requires coordinated change."
                if coordinated_caller_change
                else "Direct caller or reverse dependency may require coordinated change.",
                0.90 if coordinated_caller_change else 0.82,
                coordinated_caller_change or risk_level in {"high", "critical"},
            ))
        for path in tests:
            obligations.append(FileObligation(path, "verify", "Related regression test mapped by repository evidence.", 0.92, True))

        preserve_paths: list[str] = []
        for non_goal in non_goals:
            for path in repository.files:
                if PurePosixPath(path).name.lower() in str(non_goal).lower():
                    preserve_paths.append(path)
                    obligations.append(FileObligation(path, "preserve", f"Explicit non-goal: {non_goal}", 1.0, True))
        preserved = set(preserve_paths)

        required_change_files: list[str] = []
        mutation_terms = ("fix", "implement", "add", "change", "refactor", "migrate", "remove", "rename", "update", "repair")
        if any(term in lowered for term in mutation_terms):
            # Explicit source files are hard change obligations unless the same
            # objective explicitly protects them as a non-goal. Decisive files
            # remain inspect obligations because a correct solution may change a caller.
            required_change_files = [
                path for path in explicit
                if path not in preserved
                and (not repository.files.get(path, None) or not repository.files[path].test_file)
            ]
            if coordinated_caller_change:
                required_change_files.extend(path for path in caller_paths if path not in preserved)
            if normalized_task in {"migration", "framework_migration"}:
                migration_surface = [
                    path for path, record in repository.files.items()
                    if record.config_file or record.migration_file
                ]
                for path in migration_surface:
                    obligations.append(FileObligation(
                        path, "change", "Framework/configuration migration surface must be reconciled.",
                        0.88, True,
                    ))
                required_change_files.extend(path for path in migration_surface if path not in preserved)

        candidate_paths = set([*explicit, *decisive, *caller_paths, *tests])
        roots = cls._minimal_roots(candidate_paths)
        if not roots:
            roots = ["."]

        normalized_risk = str(risk_level).lower()
        hard = (
            normalized_risk in {"high", "critical"}
            or normalized_task in {"migration", "security_remediation"}
            or len(explicit) >= 2
            or (normalized_task in {"bug_repair", "refactor"} and len(caller_paths) >= 2)
        )
        minimum_changed = 1 if any(term in lowered for term in mutation_terms) else 0
        if coordinated_caller_change and caller_paths:
            minimum_changed = max(2, min(len(set(required_change_files)), len(caller_paths) + 1))
        invariants = [
            "No changed path may escape the derived change roots.",
            "Every required verification file must be exercised through a recorded check.",
            "Unexpected files block completion rather than being silently accepted.",
            "Files protected by explicit non-goals must remain content-identical.",
        ]
        if coordinated_caller_change:
            invariants.extend([
                "No statically discovered caller may remain on the superseded API contract.",
                "Repository-wide changes require targeted verification of definitions and all mapped callers.",
            ])
        if normalized_task in {"migration", "framework_migration"}:
            invariants.append("Deprecated framework/configuration surface must be removed or explicitly compatibility-pinned.")
        return MultiFileCompletionContract(
            schema_version="nexus.multifile-completion.v3",
            objective=objective,
            repository_tree_hash=repository.tree_hash,
            obligations=cls._dedupe_obligations(obligations),
            allowed_change_roots=roots,
            required_change_files=sorted(set(required_change_files)),
            required_verification_files=sorted(set(tests)),
            invariants=invariants,
            minimum_changed_files=minimum_changed,
            enforcement_mode="hard" if hard else "advisory",
            task_type=normalized_task,
            risk_level=normalized_risk,
        )

    @staticmethod
    def _minimal_roots(paths: Iterable[str]) -> list[str]:
        roots: set[str] = set()
        for path in paths:
            p = PurePosixPath(path)
            if len(p.parts) > 1:
                roots.add(p.parts[0])
            elif p.parts:
                roots.add(p.parts[0])
        if len(roots) > 8:
            return ["."]
        return sorted(roots)

    @classmethod
    def _dedupe(cls, items: Iterable[str]) -> list[str]:
        normalized: list[str] = []
        for item in items:
            if not str(item).strip():
                continue
            try:
                normalized.append(MultiFileCompletionContract._normalize(str(item)))
            except ValueError:
                continue
        return list(dict.fromkeys(normalized))

    @staticmethod
    def _dedupe_obligations(items: Iterable[FileObligation]) -> list[FileObligation]:
        strongest: dict[tuple[str, str], FileObligation] = {}
        for item in items:
            key = (item.path, item.obligation)
            prior = strongest.get(key)
            if prior is None or (item.blocking, item.confidence) > (prior.blocking, prior.confidence):
                strongest[key] = item
        return sorted(strongest.values(), key=lambda item: (item.path, item.obligation))
