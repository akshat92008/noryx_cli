"""
Symbol Rename Engine — Sprint 8.

Implements qualification-grade symbol renaming across a repository.

Key principles:
- Does NOT perform blind global text replacement.
- Distinguishes code symbol from user-facing string, serialized field,
  database field, configuration key, and documentation reference.
- Surfaces uncertainty for dynamic references.
- Produces a list of PlannedFileChange objects ready for an EngineeringChangeSet.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus.multifile.contracts import (
    ChangeType,
    PlannedFileChange,
    Reference,
)

logger = logging.getLogger(__name__)


class RenameKind(str):
    """Classification of a rename target."""
    CODE_SYMBOL = "CODE_SYMBOL"
    USER_FACING_STRING = "USER_FACING_STRING"
    SERIALIZED_FIELD = "SERIALIZED_FIELD"
    DATABASE_FIELD = "DATABASE_FIELD"
    CONFIGURATION_KEY = "CONFIGURATION_KEY"
    DOCUMENTATION_REFERENCE = "DOCUMENTATION_REFERENCE"
    CLI_FLAG = "CLI_FLAG"
    DYNAMIC_REFERENCE = "DYNAMIC_REFERENCE"


@dataclass
class RenameOccurrence:
    """A single occurrence of a symbol that needs to be renamed."""
    path: str
    line: int
    column: int = 0
    context: str = ""
    kind: str = RenameKind.CODE_SYMBOL
    dynamic: bool = False
    confidence: float = 1.0
    raw_snippet: str = ""


@dataclass
class RenameAnalysis:
    """Complete analysis of a rename operation before execution."""
    old_name: str
    new_name: str
    definition_occurrences: list[RenameOccurrence] = field(default_factory=list)
    import_occurrences: list[RenameOccurrence] = field(default_factory=list)
    caller_occurrences: list[RenameOccurrence] = field(default_factory=list)
    test_occurrences: list[RenameOccurrence] = field(default_factory=list)
    documentation_occurrences: list[RenameOccurrence] = field(default_factory=list)
    dynamic_occurrences: list[RenameOccurrence] = field(default_factory=list)
    string_occurrences: list[RenameOccurrence] = field(default_factory=list)  # NOT auto-renamed
    serialized_field_occurrences: list[RenameOccurrence] = field(default_factory=list)  # requires decision
    config_key_occurrences: list[RenameOccurrence] = field(default_factory=list)  # requires decision
    unresolved_warnings: list[str] = field(default_factory=list)

    @property
    def safe_occurrences(self) -> list[RenameOccurrence]:
        """Occurrences safe to rename automatically."""
        return (
            self.definition_occurrences
            + self.import_occurrences
            + self.caller_occurrences
            + self.test_occurrences
        )

    @property
    def requires_review(self) -> list[RenameOccurrence]:
        """Occurrences that require human review before renaming."""
        return (
            self.dynamic_occurrences
            + self.serialized_field_occurrences
            + self.config_key_occurrences
        )


class SymbolRenameEngine:
    """Discovers all rename targets and classifies them before mutation.

    Usage:
        engine = SymbolRenameEngine(repo_root)
        analysis = engine.analyze("OldName", "NewName")
        changes = engine.to_planned_changes(analysis)
    """

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        old_name: str,
        new_name: str,
        *,
        source_extensions: tuple[str, ...] = (".py", ".ts", ".tsx", ".js", ".jsx"),
        doc_extensions: tuple[str, ...] = (".md", ".rst", ".txt"),
        config_extensions: tuple[str, ...] = (".yaml", ".yml", ".toml", ".json", ".ini"),
    ) -> RenameAnalysis:
        """Scan the repository and classify all occurrences of old_name."""
        analysis = RenameAnalysis(old_name=old_name, new_name=new_name)

        code_pattern = re.compile(r"\b" + re.escape(old_name) + r"\b")
        string_pattern = re.compile(r"""['""]""" + re.escape(old_name) + r"""['""]""")
        import_pattern = re.compile(
            r"(?:^|\s)(?:import|from)\s+.*\b" + re.escape(old_name) + r"\b", re.MULTILINE
        )

        for path in self.repo_root.rglob("*"):
            if not path.is_file():
                continue
            rel = str(path.relative_to(self.repo_root))
            if any(skip in rel for skip in (".git", ".venv", "__pycache__")):
                continue

            suffix = path.suffix.lower()

            if suffix in source_extensions:
                self._scan_source_file(
                    path, rel, old_name, analysis,
                    code_pattern, string_pattern, import_pattern
                )
            elif suffix in doc_extensions:
                self._scan_doc_file(path, rel, old_name, analysis)
            elif suffix in config_extensions:
                self._scan_config_file(path, rel, old_name, analysis)

        return analysis

    def to_planned_changes(
        self,
        analysis: RenameAnalysis,
        *,
        include_docs: bool = True,
        include_config: bool = False,  # config renames require explicit decision
    ) -> list[PlannedFileChange]:
        """Convert a RenameAnalysis into PlannedFileChange objects."""
        files_to_change: dict[str, list[RenameOccurrence]] = {}

        for occ in analysis.safe_occurrences:
            files_to_change.setdefault(occ.path, []).append(occ)

        if include_docs:
            for occ in analysis.documentation_occurrences:
                files_to_change.setdefault(occ.path, []).append(occ)

        if include_config:
            for occ in analysis.config_key_occurrences:
                files_to_change.setdefault(occ.path, []).append(occ)

        planned: list[PlannedFileChange] = []
        for file_path, occurrences in files_to_change.items():
            kinds = list({occ.kind for occ in occurrences})
            planned.append(
                PlannedFileChange(
                    path=file_path,
                    reason=f"Rename '{analysis.old_name}' → '{analysis.new_name}': {', '.join(kinds)}",
                    change_type=ChangeType.RENAME,
                    relevant_symbols=[analysis.old_name, analysis.new_name],
                    confidence=min(occ.confidence for occ in occurrences),
                    notes=f"{len(occurrences)} occurrence(s) to rename.",
                )
            )

        return planned

    def apply_rename(
        self,
        path: str,
        old_name: str,
        new_name: str,
        *,
        safe_only: bool = True,
    ) -> tuple[bool, str, int]:
        """Apply a rename to a single file.

        Returns (success, detail, replacements_made).
        When safe_only=True, does NOT rename strings or dynamic references.
        """
        full_path = self.repo_root / path
        if not full_path.exists():
            return False, f"File not found: {path}", 0

        try:
            content = full_path.read_text(encoding="utf-8")
        except OSError as exc:
            return False, str(exc), 0

        if safe_only:
            # Only rename code symbols, not string literals
            new_content, count = _safe_symbol_replace(old_name, new_name, content)
        else:
            new_content = content.replace(old_name, new_name)
            count = content.count(old_name)

        if count == 0:
            return True, "No occurrences found", 0

        try:
            full_path.write_text(new_content, encoding="utf-8")
            return True, f"Renamed {count} occurrence(s)", count
        except OSError as exc:
            return False, str(exc), 0

    # ------------------------------------------------------------------
    # Internal scanners
    # ------------------------------------------------------------------

    def _scan_source_file(
        self,
        path: Path,
        rel: str,
        old_name: str,
        analysis: RenameAnalysis,
        code_pattern: re.Pattern,
        string_pattern: re.Pattern,
        import_pattern: re.Pattern,
    ) -> None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return

        is_test = "test_" in rel or rel.startswith("tests/")

        for i, line in enumerate(content.splitlines(), start=1):
            # Import statements
            if import_pattern.search(line):
                analysis.import_occurrences.append(
                    RenameOccurrence(
                        path=rel, line=i, kind=RenameKind.CODE_SYMBOL,
                        raw_snippet=line.strip()[:120],
                    )
                )
                continue

            if not code_pattern.search(line):
                continue

            # Dynamic reference (getattr, __import__, etc.) — check BEFORE string patterns
            if _is_dynamic(old_name, line):
                analysis.dynamic_occurrences.append(
                    RenameOccurrence(
                        path=rel, line=i, kind=RenameKind.DYNAMIC_REFERENCE,
                        dynamic=True, confidence=0.3,
                        raw_snippet=line.strip()[:120],
                    )
                )
                analysis.unresolved_warnings.append(
                    f"{rel}:{i}: dynamic reference to '{old_name}' — cannot safely auto-rename."
                )
                continue

            # String literal (but not a definition, import, or dynamic ref)
            if string_pattern.search(line) and not re.search(
                r"(?:def|class|import|from)\s+" + re.escape(old_name), line
            ):
                analysis.string_occurrences.append(
                    RenameOccurrence(
                        path=rel, line=i, kind=RenameKind.USER_FACING_STRING,
                        dynamic=False, confidence=0.5,
                        raw_snippet=line.strip()[:120],
                    )
                )
                analysis.unresolved_warnings.append(
                    f"{rel}:{i}: string literal '{old_name}' found — "
                    "may be a user-facing string, serialized field, or log message. Review before renaming."
                )
                continue

            # Definition or caller
            if is_test:
                analysis.test_occurrences.append(
                    RenameOccurrence(
                        path=rel, line=i, kind=RenameKind.CODE_SYMBOL,
                        raw_snippet=line.strip()[:120],
                    )
                )
            elif re.search(r"(?:def|class)\s+" + re.escape(old_name), line):
                analysis.definition_occurrences.append(
                    RenameOccurrence(
                        path=rel, line=i, kind=RenameKind.CODE_SYMBOL,
                        raw_snippet=line.strip()[:120],
                    )
                )
            else:
                analysis.caller_occurrences.append(
                    RenameOccurrence(
                        path=rel, line=i, kind=RenameKind.CODE_SYMBOL,
                        raw_snippet=line.strip()[:120],
                    )
                )

    def _scan_doc_file(
        self, path: Path, rel: str, old_name: str, analysis: RenameAnalysis
    ) -> None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for i, line in enumerate(content.splitlines(), start=1):
            if old_name in line:
                analysis.documentation_occurrences.append(
                    RenameOccurrence(
                        path=rel, line=i, kind=RenameKind.DOCUMENTATION_REFERENCE,
                        confidence=0.8, raw_snippet=line.strip()[:120],
                    )
                )

    def _scan_config_file(
        self, path: Path, rel: str, old_name: str, analysis: RenameAnalysis
    ) -> None:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        for i, line in enumerate(content.splitlines(), start=1):
            if old_name in line:
                analysis.config_key_occurrences.append(
                    RenameOccurrence(
                        path=rel, line=i, kind=RenameKind.CONFIGURATION_KEY,
                        confidence=0.6, raw_snippet=line.strip()[:120],
                    )
                )
                analysis.unresolved_warnings.append(
                    f"{rel}:{i}: configuration reference to '{old_name}' — "
                    "requires compatibility analysis before renaming."
                )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_symbol_replace(old: str, new: str, content: str) -> tuple[str, int]:
    """Replace code symbol occurrences only — not string literals."""
    pattern = re.compile(r"\b" + re.escape(old) + r"\b")
    count = [0]

    def replacer(m: re.Match) -> str:
        # Check if inside a string — crude heuristic: look at surrounding chars
        start = m.start()
        preceding = content[max(0, start - 1):start]
        if preceding in ('"', "'", "`"):
            return m.group(0)  # Don't replace inside strings
        count[0] += 1
        return new

    new_content = pattern.sub(replacer, content)
    return new_content, count[0]


def _is_dynamic(symbol: str, line: str) -> bool:
    """Heuristic: is the symbol reference dynamic?"""
    patterns = [
        rf"getattr\([^,]+,\s*['\"]?{re.escape(symbol)}",
        rf"__import__\([^)]*{re.escape(symbol)}",
        rf"globals\(\)\[['\"]?{re.escape(symbol)}",
        rf"locals\(\)\[['\"]?{re.escape(symbol)}",
        rf"importlib\.import_module\([^)]*{re.escape(symbol)}",
    ]
    return any(re.search(p, line) for p in patterns)
