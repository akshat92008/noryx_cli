"""Canonical data structures for Nexus Repository Intelligence — Sprint 5."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class TaskIntent(str, Enum):
    BUG_REPAIR = "bug_repair"
    FEATURE_IMPLEMENTATION = "feature_implementation"
    REFACTOR = "refactor"
    MIGRATION = "migration"
    TEST_CREATION = "test_creation"
    TEST_REPAIR = "test_repair"
    DEPENDENCY_UPGRADE = "dependency_upgrade"
    DOCUMENTATION = "documentation"
    PERFORMANCE_OPTIMIZATION = "performance_optimization"
    SECURITY_FIX = "security_fix"
    CONFIGURATION_CHANGE = "configuration_change"
    INVESTIGATION = "investigation"
    EXPLANATION = "explanation"
    GENERAL = "general"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class FileContext:
    path: str
    last_accessed: str = ""
    access_count: int = 0
    was_edited: bool = False
    relevance_score: float = 0.0
    summary: str = ""
    line_count: int = 0
    language: str = ""
    imports: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    content_sha256: str = ""
    modified_ns: int = 0

    @property
    def basename(self) -> str:
        return Path(self.path).name


@dataclass
class ArchitectureMap:
    project_root: str = ""
    project_type: str = ""
    framework: str = ""
    entry_points: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)
    test_directories: list[str] = field(default_factory=list)
    source_directories: list[str] = field(default_factory=list)
    modules: dict[str, list[str]] = field(default_factory=dict)

    def to_summary(self) -> str:
        lines = [f"Project: {Path(self.project_root).name}"]
        if self.project_type:
            lines.append(f"Type: {self.project_type}")
        if self.framework:
            lines.append(f"Framework: {self.framework}")
        return " | ".join(lines)


@dataclass
class SymbolReference:
    symbol: str
    file_path: str
    line: int
    column: int = 0
    kind: str = "reference"
    confidence: float = 1.0


@dataclass
class ImportEdge:
    source_file: str
    target: str  # imported module/file name
    symbol_name: str | None = None
    is_direct: bool = True
    line: int = 0


@dataclass
class CallEdge:
    caller_symbol: str
    callee_symbol: str
    source_file: str
    line: int = 0
    confidence: float = 1.0


@dataclass
class InheritanceEdge:
    child_symbol: str
    parent_symbol: str
    source_file: str


@dataclass
class TestRelationship:
    source_file: str
    test_file: str
    relationship_type: str  # DIRECT_IMPORT, DIRECT_SYMBOL_REFERENCE, COVERAGE_OBSERVED, NAMING_CONVENTION, CO_CHANGE_HISTORY, PACKAGE_LEVEL, HEURISTIC
    confidence: float = 1.0
    reason: str = ""


@dataclass
class ConfigurationRelationship:
    config_file: str
    target_path: str
    relationship_type: str  # CONFIGURES, VALIDATES, GENERATES, SERIALIZES, DEPLOYS, MIGRATES, SECURES, BUILDS
    confidence: float = 1.0
    details: str = ""


@dataclass
class HistoricalRelationship:
    source_file: str
    related_file: str
    co_change_count: int
    last_co_change_date: str = ""
    score: float = 0.0


@dataclass
class ArchitectureBoundary:
    layer_name: str  # ui, api, domain, persistence, provider_adapter, controller, verification, plugin
    files: list[str] = field(default_factory=list)
    allowed_imports: list[str] = field(default_factory=list)
    forbidden_imports: list[str] = field(default_factory=list)


@dataclass
class RiskAnnotation:
    path: str
    risk_level: RiskLevel
    reasons: list[str] = field(default_factory=list)
    mutation_risk: str = "normal"
    verification_requirements: list[str] = field(default_factory=list)


@dataclass
class RepositorySymbol:
    name: str
    kind: str  # module, class, function, method, interface, type, constant, decorator
    file_path: str
    line: int
    end_line: int = 0
    qualified_name: str = ""
    signature: str = ""
    visibility: str = "public"  # public, private, protected
    parent_symbol: str | None = None
    docstring: str = ""
    content_hash: str = ""
    imports: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    callers: list[str] = field(default_factory=list)
    callees: list[str] = field(default_factory=list)


@dataclass
class RepositoryFile:
    path: str
    language: str | None = None
    size_bytes: int = 0
    content_hash: str = ""
    mtime_ns: int = 0
    tracked: bool = True
    generated: bool = False
    vendored: bool = False
    binary: bool = False
    protected: bool = False
    test_file: bool = False
    config_file: bool = False
    migration_file: bool = False
    entrypoint: bool = False
    risk_level: RiskLevel = RiskLevel.LOW
    category: str = "source"  # source, test, configuration, schema, migration, documentation, generated, vendored, binary, lockfile, build_artifact, fixture, script, infrastructure, deployment, secret_sensitive, unknown
    imports: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    database_models: list[str] = field(default_factory=list)
    symbols: list[RepositorySymbol] = field(default_factory=list)
    parse_error: str = ""

    @property
    def is_test(self) -> bool:
        return self.test_file

    @property
    def is_config(self) -> bool:
        return self.config_file


@dataclass
class ContextCandidate:
    path: str
    source_signal: str  # user_explicit, stack_trace, exact_symbol, import_graph, reverse_dep, test_mapping, config_rel, git_history, semantic
    relationship: str
    confidence: float
    estimated_tokens: int
    risk: RiskLevel = RiskLevel.LOW
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0


@dataclass
class ContextFile:
    path: str
    language: str | None
    is_test: bool
    is_config: bool
    excerpt: str
    start_line: int = 1
    end_line: int = 1
    is_full_content: bool = False
    selection_reason: str = ""
    estimated_tokens: int = 0


@dataclass
class ContextSymbol:
    name: str
    kind: str
    file_path: str
    line: int
    signature: str = ""
    relevance_reason: str = ""


@dataclass
class ContextRelationship:
    source: str
    target: str
    relationship_type: str
    description: str = ""


@dataclass
class ContextBundle:
    task_intent: TaskIntent
    repository_tree_hash: str
    files: list[ContextFile] = field(default_factory=list)
    symbols: list[ContextSymbol] = field(default_factory=list)
    relationships: list[ContextRelationship] = field(default_factory=list)
    tests: list[TestRelationship] = field(default_factory=list)
    constraints: list[ArchitectureBoundary] = field(default_factory=list)
    risks: list[RiskAnnotation] = field(default_factory=list)
    estimated_tokens: int = 0
    confidence: float = 1.0
    limitations: list[str] = field(default_factory=list)
    omitted_candidates: list[str] = field(default_factory=list)
    selection_rationales: dict[str, list[str]] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_formatted_prompt(self) -> str:
        """Render prompt context for model presentation."""
        sections = [f"[REPOSITORY CONTEXT BUNDLE - Intent: {self.task_intent.value}]"]
        if self.constraints:
            sections.append("[ARCHITECTURE BOUNDARIES]")
            for boundary in self.constraints:
                sections.append(f"- Layer: {boundary.layer_name} ({len(boundary.files)} files)")
        if self.risks:
            sections.append("[RISK ANNOTATIONS]")
            for risk in self.risks:
                sections.append(f"- {risk.path} [{risk.risk_level.value.upper()}]: {', '.join(risk.reasons)}")
        if self.tests:
            sections.append("[RELATED TESTS]")
            for test_rel in self.tests:
                sections.append(f"- {test_rel.test_file} ({test_rel.relationship_type}, conf={test_rel.confidence}) -> {test_rel.source_file}")
        
        sections.append("\n[DECISIVE FILES]")
        for ctx_file in self.files:
            sections.append(
                f"\n--- {ctx_file.path} [{ctx_file.language or 'unknown'}] "
                f"(lines {ctx_file.start_line}-{ctx_file.end_line}, reason: {ctx_file.selection_reason}) ---\n"
                f"{ctx_file.excerpt}"
            )
        
        if self.omitted_candidates:
            sections.append(f"\n[OMITTED CONTEXT (Budget Limit)]\nOmitted {len(self.omitted_candidates)} candidates: {', '.join(self.omitted_candidates[:10])}")
        
        return "\n".join(sections)


@dataclass
class RepositorySnapshot:
    repository_id: str
    root: Path
    revision: str | None = None
    tree_hash: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    language_profiles: list[str] = field(default_factory=list)
    files: dict[str, RepositoryFile] = field(default_factory=dict)
    symbols: dict[str, RepositorySymbol] = field(default_factory=dict)
    relationships: list[Any] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
