"""Repository Intelligence package for Nexus CLI — Sprint 5."""

from nexus.intelligence.repository.model import (
    ArchitectureBoundary,
    CallEdge,
    ConfigurationRelationship,
    ContextBundle,
    ContextCandidate,
    ContextFile,
    ContextRelationship,
    ContextSymbol,
    HistoricalRelationship,
    ImportEdge,
    InheritanceEdge,
    RepositoryFile,
    RepositorySnapshot,
    RepositorySymbol,
    RiskAnnotation,
    SymbolReference,
    TaskIntent,
    TestRelationship,
)

__all__ = [
    "ArchitectureBoundary",
    "CallEdge",
    "ConfigurationRelationship",
    "ContextBundle",
    "ContextCandidate",
    "ContextFile",
    "ContextRelationship",
    "ContextSymbol",
    "HistoricalRelationship",
    "ImportEdge",
    "InheritanceEdge",
    "RepositoryFile",
    "RepositorySnapshot",
    "RepositorySymbol",
    "RiskAnnotation",
    "SymbolReference",
    "TaskIntent",
    "TestRelationship",
]

from .snapshot import WorkspaceSnapshot, workspace_revision
