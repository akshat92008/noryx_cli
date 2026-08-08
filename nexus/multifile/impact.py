"""
Impact Analyzer — Sprint 8.

Queries the Sprint 5 RepositoryIntelligence engine to determine which files,
symbols, tests, and configuration items are affected by a proposed contract change.

Produces a typed ImpactReport with category classifications (MUST_CHANGE,
MUST_VERIFY, LIKELY_AFFECTED, POSSIBLY_AFFECTED, UNRESOLVED, OUT_OF_SCOPE).

Design invariants:
- Heuristic relationships are NOT treated as certain.
- Dynamic references are surfaced explicitly with dynamic=True.
- Unresolved consumers block high-confidence assertions.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from nexus.multifile.contracts import (
    ContractChange,
    ContractScope,
    ContractType,
    ImpactCategory,
    ImpactReport,
    ImpactTarget,
    Reference,
    Risk,
    SymbolReference,
    TestTarget,
)

logger = logging.getLogger(__name__)


class ImpactAnalyzer:
    """Queries repository intelligence to build ImpactReport for a set of contract changes."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        repo_intelligence: Any = None,   # nexus.intelligence.repository.engine.RepositoryIntelligence
    ):
        self.repo_root = Path(repo_root)
        self._ri = repo_intelligence

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        contract_changes: list[ContractChange],
        repository_snapshot_id: str = "",
    ) -> ImpactReport:
        """Run complete impact analysis for a list of contract changes."""
        report = ImpactReport(repository_snapshot_id=repository_snapshot_id)

        for cc in contract_changes:
            self._analyze_one_contract(cc, report)

        # Deduplicate and rank
        report.directly_affected = _deduplicate(report.directly_affected)
        report.transitively_affected = _deduplicate(report.transitively_affected)
        report.potentially_affected = _deduplicate(report.potentially_affected)
        report.tests_required = _deduplicate_tests(report.tests_required)
        report.contracts_changed = contract_changes

        # Confidence degrades when there are unresolved dynamic dependencies
        if report.unresolved_dynamic_dependencies:
            dynamic_penalty = min(
                0.3, 0.1 * len(report.unresolved_dynamic_dependencies)
            )
            report.confidence = max(0.3, 1.0 - dynamic_penalty)

        return report

    def discover_callers(self, symbol: str, definition_path: str) -> list[ImpactTarget]:
        """Find all callers of a symbol in the repository."""
        targets: list[ImpactTarget] = []

        if self._ri and hasattr(self._ri, "files"):
            # Use the Sprint 5 graph
            for path, repo_file in self._ri.files.items():
                if path == definition_path:
                    continue
                content = _read_file_safe(self.repo_root / path)
                if _symbol_referenced_in(symbol, content):
                    category = ImpactCategory.MUST_CHANGE
                    dynamic = _is_dynamic_reference(symbol, content)
                    if dynamic:
                        category = ImpactCategory.UNRESOLVED
                    targets.append(
                        ImpactTarget(
                            path=path,
                            symbol=symbol,
                            category=category,
                            reason=f"References symbol '{symbol}' from '{definition_path}'",
                            dynamic=dynamic,
                        )
                    )
        else:
            # Fallback: grep the repo
            targets = self._grep_symbol(symbol, exclude_path=definition_path)

        return targets

    def discover_reverse_imports(self, module_path: str) -> list[ImpactTarget]:
        """Find files that import from module_path."""
        module_name = _path_to_module(module_path)
        targets: list[ImpactTarget] = []

        for py_file in self.repo_root.rglob("*.py"):
            rel = str(py_file.relative_to(self.repo_root))
            if rel == module_path:
                continue
            content = _read_file_safe(py_file)
            if (
                f"import {module_name}" in content
                or f"from {module_name}" in content
                or f"from {module_name.replace('.', '/')}" in content
            ):
                targets.append(
                    ImpactTarget(
                        path=rel,
                        symbol="",
                        category=ImpactCategory.MUST_VERIFY,
                        reason=f"Imports from '{module_path}'",
                    )
                )

        return targets

    def discover_test_coverage(self, file_path: str) -> list[TestTarget]:
        """Find test files that exercise a given source file."""
        stem = Path(file_path).stem
        tests: list[TestTarget] = []

        for test_file in self.repo_root.rglob("test_*.py"):
            rel = str(test_file.relative_to(self.repo_root))
            content = _read_file_safe(test_file)
            if stem in content or file_path in content:
                tests.append(
                    TestTarget(
                        path=rel,
                        reason=f"Covers '{file_path}'",
                        level=1,
                    )
                )

        return tests

    def discover_configuration_references(self, key: str) -> list[ImpactTarget]:
        """Find configuration files or code that references a configuration key."""
        targets: list[ImpactTarget] = []
        pattern = re.compile(re.escape(key))

        config_extensions = {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".env"}

        for f in self.repo_root.rglob("*"):
            if not f.is_file():
                continue
            rel = str(f.relative_to(self.repo_root))
            if ".git" in rel or ".venv" in rel or "__pycache__" in rel:
                continue
            if f.suffix in config_extensions or f.name.startswith(".env"):
                content = _read_file_safe(f)
                if pattern.search(content):
                    targets.append(
                        ImpactTarget(
                            path=rel,
                            symbol=key,
                            category=ImpactCategory.MUST_CHANGE,
                            reason=f"Configuration key '{key}' referenced",
                        )
                    )

        return targets

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _analyze_one_contract(self, cc: ContractChange, report: ImpactReport) -> None:
        """Populate the report for a single ContractChange."""
        sym = cc.definition.symbol
        def_path = cc.definition.path

        # 1. Direct callers
        callers = self.discover_callers(sym, definition_path=def_path)
        for t in callers:
            if t.dynamic:
                report.unresolved_dynamic_dependencies.append(
                    f"{t.path}: dynamic reference to '{sym}'"
                )
                t.category = ImpactCategory.UNRESOLVED
                report.potentially_affected.append(t)
            else:
                report.directly_affected.append(t)

        # 2. Reverse imports
        rev_imports = self.discover_reverse_imports(def_path)
        for t in rev_imports:
            if not _already_in(t.path, report.directly_affected):
                report.transitively_affected.append(t)

        # 3. Tests
        tests = self.discover_test_coverage(def_path)
        report.tests_required.extend(tests)

        # 4. Configuration references (for config-key contract changes)
        if cc.contract_type in (
            ContractType.CONFIGURATION_KEY,
            ContractType.ENVIRONMENT_VARIABLE,
            ContractType.CLI_FLAG,
        ):
            config_refs = self.discover_configuration_references(sym)
            for t in config_refs:
                if not _already_in(t.path, report.directly_affected):
                    report.directly_affected.append(t)

        # 5. Architecture risks for external contracts
        if cc.scope in (ContractScope.EXTERNAL_API, ContractScope.PACKAGE_PUBLIC):
            report.architecture_risks.append(
                Risk(
                    risk_id=f"external-contract-{cc.contract_id}",
                    description=f"Contract '{sym}' is {cc.scope.value} — external consumers may not be discoverable.",
                    severity="HIGH",
                    mitigation="Inspect public documentation and changelog for known consumers.",
                )
            )
            report.unresolved_dynamic_dependencies.append(
                f"External contract '{sym}' — cannot verify all external consumers via static analysis."
            )

    def _grep_symbol(self, symbol: str, exclude_path: str = "") -> list[ImpactTarget]:
        """Fallback: scan Python files for symbol references."""
        targets: list[ImpactTarget] = []
        pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")

        for py_file in self.repo_root.rglob("*.py"):
            rel = str(py_file.relative_to(self.repo_root))
            if rel == exclude_path:
                continue
            if ".venv" in rel or "__pycache__" in rel:
                continue
            content = _read_file_safe(py_file)
            # Skip files where the only reference is in comments
            non_comment_lines = [
                line for line in content.splitlines()
                if not line.strip().startswith("#")
            ]
            non_comment_content = "\n".join(non_comment_lines)
            if pattern.search(non_comment_content):
                dynamic = _is_dynamic_reference(symbol, content)
                targets.append(
                    ImpactTarget(
                        path=rel,
                        symbol=symbol,
                        category=ImpactCategory.UNRESOLVED if dynamic else ImpactCategory.MUST_CHANGE,
                        reason=f"grep: symbol '{symbol}' found",
                        dynamic=dynamic,
                    )
                )

        return targets


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def _read_file_safe(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _path_to_module(path: str) -> str:
    return path.replace("/", ".").replace("\\", ".").removesuffix(".py")


def _symbol_referenced_in(symbol: str, content: str) -> bool:
    pattern = re.compile(r"\b" + re.escape(symbol) + r"\b")
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue  # skip pure comment lines
        if pattern.search(line):
            return True
    return False


def _is_dynamic_reference(symbol: str, content: str) -> bool:
    """Heuristic: detect patterns that suggest dynamic/string-based reference."""
    dynamic_patterns = [
        rf'getattr\([^)]*["\']?{re.escape(symbol)}["\']?',
        rf'__import__\([^)]*{re.escape(symbol)}',
        rf'importlib\.import_module\([^)]*{re.escape(symbol)}',
        rf'globals\(\)\[["\']?{re.escape(symbol)}["\']?\]',
        rf'locals\(\)\[["\']?{re.escape(symbol)}["\']?\]',
        rf'["\']' + re.escape(symbol) + r'["\']',  # string reference
    ]
    for pat in dynamic_patterns:
        if re.search(pat, content):
            return True
    return False


def _already_in(path: str, targets: list[ImpactTarget]) -> bool:
    return any(t.path == path for t in targets)


T = Any


def _deduplicate(targets: list[ImpactTarget]) -> list[ImpactTarget]:
    seen: dict[str, ImpactTarget] = {}
    for t in targets:
        key = f"{t.path}:{t.symbol}"
        if key not in seen:
            seen[key] = t
        else:
            # Prefer higher-priority category
            _category_priority = {
                ImpactCategory.MUST_CHANGE: 0,
                ImpactCategory.MUST_VERIFY: 1,
                ImpactCategory.LIKELY_AFFECTED: 2,
                ImpactCategory.POSSIBLY_AFFECTED: 3,
                ImpactCategory.UNRESOLVED: 4,
                ImpactCategory.OUT_OF_SCOPE: 5,
            }
            if _category_priority.get(t.category, 9) < _category_priority.get(seen[key].category, 9):
                seen[key] = t
    return list(seen.values())


def _deduplicate_tests(tests: list[TestTarget]) -> list[TestTarget]:
    seen: set[str] = set()
    result: list[TestTarget] = []
    for t in tests:
        if t.path not in seen:
            seen.add(t.path)
            result.append(t)
    return result
