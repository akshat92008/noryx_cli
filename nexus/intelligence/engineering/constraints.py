"""Typed compilation of natural-language engineering constraints.

The compiler intentionally supports a conservative, explicit subset.  Ambiguous
constraints remain visible as unresolved hard constraints instead of silently becoming
planning prose.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Iterable

from nexus.path_grammar import PATH_PATTERN, extract_repository_paths

_PATH = PATH_PATTERN


class ConstraintKind(str, Enum):
    FORBID_FILE_WRITE = "FORBID_FILE_WRITE"
    FORBID_DIRECTORY_WRITE = "FORBID_DIRECTORY_WRITE"
    FORBID_SCHEMA_CHANGE = "FORBID_SCHEMA_CHANGE"
    FORBID_PUBLIC_API_CHANGE = "FORBID_PUBLIC_API_CHANGE"
    FORBID_NEW_DEPENDENCY = "FORBID_NEW_DEPENDENCY"
    FORBID_AUTH_CHANGE = "FORBID_AUTH_CHANGE"
    REQUIRE_BACKWARD_COMPATIBILITY = "REQUIRE_BACKWARD_COMPATIBILITY"
    PRESERVE_BEHAVIOR = "PRESERVE_BEHAVIOR"
    UNRESOLVED_HARD_CONSTRAINT = "UNRESOLVED_HARD_CONSTRAINT"


@dataclass(frozen=True)
class CompiledConstraint:
    id: str
    kind: ConstraintKind
    source_text: str
    target: str = ""
    hard: bool = True

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "CompiledConstraint":
        return cls(
            id=str(data["id"]),
            kind=ConstraintKind(str(data["kind"])),
            source_text=str(data.get("source_text", "")),
            target=str(data.get("target", "")),
            hard=bool(data.get("hard", True)),
        )

    def forbidden_patterns(self) -> tuple[str, ...]:
        if self.kind == ConstraintKind.FORBID_FILE_WRITE and self.target:
            return (self.target.replace("\\", "/"),)
        if self.kind == ConstraintKind.FORBID_DIRECTORY_WRITE and self.target:
            target = self.target.rstrip("/\\").replace("\\", "/")
            return (f"{target}/**",)
        if self.kind == ConstraintKind.FORBID_SCHEMA_CHANGE:
            return (
                "**/migrations/**",
                "**/migration/**",
                "**/*.sql",
                "**/schema.*",
                "**/alembic/**",
                "**/prisma/schema.prisma",
            )
        if self.kind == ConstraintKind.FORBID_NEW_DEPENDENCY:
            return (
                "requirements*.txt",
                "pyproject.toml",
                "uv.lock",
                "poetry.lock",
                "Pipfile*",
                "package.json",
                "package-lock.json",
                "pnpm-lock.yaml",
                "yarn.lock",
                "go.mod",
                "go.sum",
                "Cargo.toml",
                "Cargo.lock",
                "pom.xml",
                "build.gradle*",
            )
        if self.kind == ConstraintKind.FORBID_AUTH_CHANGE:
            return (
                "**/auth/**",
                "**/authentication/**",
                "**/authorization/**",
                "**/*auth*.py",
                "**/*auth*.ts",
                "**/*auth*.js",
            )
        return ()


@dataclass(frozen=True)
class ConstraintCompilation:
    constraints: tuple[CompiledConstraint, ...]
    unresolved: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "constraints": [item.to_dict() for item in self.constraints],
            "unresolved": list(self.unresolved),
        }

    def forbidden_patterns(self) -> list[str]:
        patterns: list[str] = []
        for constraint in self.constraints:
            patterns.extend(constraint.forbidden_patterns())
        return list(dict.fromkeys(patterns))

    def is_path_forbidden(self, path: str) -> bool:
        normalized = path.replace("\\", "/")
        return any(fnmatch.fnmatch(normalized, pattern) for pattern in self.forbidden_patterns())


class ConstraintCompiler:
    """Compile explicit user restrictions into immutable policy objects."""

    _NEGATIVE_PATTERNS = (
        re.compile(
            r"\b(?:without|do\s+not|don't|dont|never|must\s+not|should\s+not|avoid)\s+"
            r"(?:changing|change|modifying|modify|editing|edit|touching|touch|altering|alter|"
            r"rewriting|rewrite|updating|update|writing|write|creating|create|deleting|delete)\s+"
            r"(?P<target>[^;\n]+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bleave\s+(?P<target>[^;\n]+?)\s+(?:untouched|unchanged|alone|as\s+is)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bkeep\s+(?P<target>[^;\n]+?)\s+(?:untouched|unchanged|stable|intact|compatible)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?P<target>[^;\n]+?)\s+must\s+remain\s+(?:untouched|unchanged|stable|intact|compatible)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bpreserve\s+(?P<target>the\s+)?(?P<value>[^;\n]+)",
            re.IGNORECASE,
        ),
    )

    @classmethod
    def compile(cls, objective: str) -> ConstraintCompilation:
        constraints: list[CompiledConstraint] = []
        unresolved: list[str] = []
        counter = 0

        def add(kind: ConstraintKind, source: str, target: str = "") -> None:
            nonlocal counter
            candidate = CompiledConstraint(
                id=f"constraint-{counter + 1:03d}",
                kind=kind,
                source_text=source.strip(),
                target=target.strip(" `\"'").replace("\\", "/"),
            )
            key = (candidate.kind, candidate.target.lower(), candidate.source_text.lower())
            if any(
                (item.kind, item.target.lower(), item.source_text.lower()) == key
                for item in constraints
            ):
                return
            counter += 1
            constraints.append(candidate)

        text = objective.strip()
        lowered = text.lower()

        # Global no-write clauses are executable policy, not merely planning
        # prose.  A wildcard FORBID_FILE_WRITE blocks every workspace path.
        if re.search(
            r"\b(?:do\s+not|don't|dont|never|without|avoid)\s+"
            r"(?:writing|write|creating|create|editing|edit|modifying|modify|changing|change|"
            r"touching|touch|altering|alter|rewriting|rewrite|updating|update|deleting|delete)\b"
            r"[^.;\n]*\b(?:any\s+)?files?\b",
            text,
            re.IGNORECASE,
        ):
            add(ConstraintKind.FORBID_FILE_WRITE, "do not write files", "**")

        if re.search(
            r"\b(?:no|without|do\s+not\s+add|don't\s+add|avoid(?:\s+adding)?)\s+(?:new|additional)?\s*dependenc",
            lowered,
        ):
            add(ConstraintKind.FORBID_NEW_DEPENDENCY, "no new dependencies")
        if re.search(
            r"\b(?:preserve|keep|leave|do\s+not\s+change|without\s+changing)\b[^.;\n]*\b(?:database\s+)?schema\b",
            lowered,
        ):
            add(ConstraintKind.FORBID_SCHEMA_CHANGE, "preserve database schema")
        if re.search(
            r"\b(?:public\s+api|api\s+contract)\b[^.;\n]*(?:unchanged|stable|compatible)|(?:preserve|keep|leave)\b[^.;\n]*\b(?:public\s+api|api\s+contract)\b",
            lowered,
        ):
            add(ConstraintKind.FORBID_PUBLIC_API_CHANGE, "keep public API unchanged")
        if re.search(r"\b(?:backward|backwards)\s+compatib", lowered) or re.search(
            r"\bmaintain\s+compatib", lowered
        ):
            add(ConstraintKind.REQUIRE_BACKWARD_COMPATIBILITY, "maintain backward compatibility")
        if re.search(
            r"\b(?:do\s+not|don't|without|avoid)\b[^.;\n]*\b(?:auth|authentication|authorization)\b",
            lowered,
        ):
            add(ConstraintKind.FORBID_AUTH_CHANGE, "do not modify authentication")

        for pattern in cls._NEGATIVE_PATTERNS:
            for match in pattern.finditer(text):
                target = (
                    match.groupdict().get("target") or match.groupdict().get("value") or ""
                ).strip()
                source = match.group(0).strip()
                paths = extract_repository_paths(target)
                if paths:
                    for path in paths:
                        add(ConstraintKind.FORBID_FILE_WRITE, source, path)
                    continue
                normalized = target.lower().strip(" `\"'")
                if normalized in {"file", "files", "any file", "any files", "all files"}:
                    add(ConstraintKind.FORBID_FILE_WRITE, source, "**")
                elif "schema" in normalized:
                    add(ConstraintKind.FORBID_SCHEMA_CHANGE, source)
                elif "dependenc" in normalized:
                    add(ConstraintKind.FORBID_NEW_DEPENDENCY, source)
                elif "public api" in normalized or "api contract" in normalized:
                    add(ConstraintKind.FORBID_PUBLIC_API_CHANGE, source)
                elif "backward" in normalized or "compatib" in normalized:
                    add(ConstraintKind.REQUIRE_BACKWARD_COMPATIBILITY, source)
                elif "behavio" in normalized or "semantics" in normalized:
                    add(ConstraintKind.PRESERVE_BEHAVIOR, source)
                elif "auth" in normalized:
                    add(ConstraintKind.FORBID_AUTH_CHANGE, source)
                elif normalized:
                    unresolved.append(source)
                    add(ConstraintKind.UNRESOLVED_HARD_CONSTRAINT, source, normalized)

        return ConstraintCompilation(tuple(constraints), tuple(dict.fromkeys(unresolved)))

    @staticmethod
    def remove_forbidden(paths: Iterable[str], compilation: ConstraintCompilation) -> list[str]:
        return [path for path in paths if not compilation.is_path_forbidden(path)]
