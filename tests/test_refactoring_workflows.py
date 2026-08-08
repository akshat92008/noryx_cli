"""Tests for refactoring workflows — ChangeDependencyGraph (Sprint 8)."""
from __future__ import annotations

import pytest
from pathlib import Path

from nexus.multifile.contracts import ChangeType, ChangeDependency, PlannedFileChange
from nexus.multifile.graph import (
    ChangeDependencyGraph,
    DependencyCycleError,
    DependencyConflictError,
    build_graph,
)
from nexus.multifile.consistency import ChangeSetConsistencyValidator
from nexus.multifile.contracts import EngineeringChangeSet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fc(path: str, change_type=ChangeType.MODIFY, symbols=(), depends_on=()) -> PlannedFileChange:
    return PlannedFileChange(
        path=path,
        reason=f"Change in {path}",
        change_type=change_type,
        relevant_symbols=list(symbols),
        depends_on=list(depends_on),
    )


# ---------------------------------------------------------------------------
# Extract class / move module
# ---------------------------------------------------------------------------


def test_extract_class_dependency_order():
    """Extracted class must be defined before its users."""
    changes = [
        fc("nexus/god_object.py"),
        fc("nexus/extracted.py", change_type=ChangeType.CREATE, depends_on=["nexus/god_object.py"]),
        fc("nexus/client.py", depends_on=["nexus/extracted.py"]),
    ]
    graph = build_graph(changes)
    order = graph.topological_sort()
    paths = [c.path for c in order]
    assert paths.index("nexus/god_object.py") < paths.index("nexus/extracted.py")
    assert paths.index("nexus/extracted.py") < paths.index("nexus/client.py")


def test_move_module_imports_updated(tmp_path):
    """Moving a module: stale imports are detected by consistency validator."""
    # Create a file that imports the old path
    old_path = tmp_path / "nexus" / "helpers.py"
    old_path.parent.mkdir(parents=True)
    old_path.write_text("def helper(): pass\n", encoding="utf-8")

    importer = tmp_path / "nexus" / "core.py"
    importer.write_text("from nexus.helpers import helper\n", encoding="utf-8")

    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="nexus/utils/helpers.py",
                reason="Move helpers to utils package",
                change_type=ChangeType.MOVE,
            ),
            # core.py NOT updated → should be detected as stale reference
        ]
    )
    # Simulate: the MOVE means nexus.helpers is gone
    # The validator checks for stale imports of the moved module
    # (We check the inverse: if we had MOVED the file, stale refs should be caught)
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    # Create the move-type change for the old path
    cs2 = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="nexus/helpers.py",
                reason="Move module",
                change_type=ChangeType.MOVE,
            )
        ]
    )
    result = validator.validate(cs2)
    # Should detect stale reference: nexus/core.py still imports nexus.helpers
    stale_paths = [r.path for r in result.stale_references]
    assert "nexus/core.py" in stale_paths


def test_split_module_creates_multiple_files():
    """Split module: multiple CREATE entries with correct dependency ordering."""
    changes = [
        fc("nexus/monolith.py"),  # original (will be emptied)
        fc("nexus/part_a.py", change_type=ChangeType.CREATE, depends_on=["nexus/monolith.py"]),
        fc("nexus/part_b.py", change_type=ChangeType.CREATE, depends_on=["nexus/monolith.py"]),
        fc("nexus/imports.py", depends_on=["nexus/part_a.py", "nexus/part_b.py"]),
    ]
    graph = build_graph(changes)
    errors = graph.validate()
    assert not errors, f"Expected no errors but got: {errors}"
    order = graph.topological_sort()
    paths = [c.path for c in order]
    assert paths.index("nexus/monolith.py") < paths.index("nexus/part_a.py")
    assert paths.index("nexus/monolith.py") < paths.index("nexus/part_b.py")


def test_cycle_detected():
    """Circular dependency between files raises DependencyCycleError."""
    graph = ChangeDependencyGraph()
    graph.add_file_change(fc("nexus/a.py", depends_on=["nexus/b.py"]))
    graph.add_file_change(fc("nexus/b.py", depends_on=["nexus/a.py"]))

    with pytest.raises(DependencyCycleError):
        graph.topological_sort()


def test_symbol_conflict_detected():
    """Two changes modifying the same symbol are a conflict."""
    graph = ChangeDependencyGraph()
    graph.add_file_change(fc("nexus/a.py", symbols=["process"]))
    graph.add_file_change(fc("nexus/b.py", symbols=["process"]))

    conflicts = graph.detect_conflicts()
    assert any(sym == "process" for _, _, sym in conflicts)


def test_parallel_safe_groups():
    """Files with no dependency can execute in parallel."""
    changes = [
        fc("nexus/a.py"),
        fc("nexus/b.py"),
        fc("nexus/c.py", depends_on=["nexus/a.py", "nexus/b.py"]),
    ]
    graph = build_graph(changes)
    groups = graph.parallel_safe_groups()
    assert len(groups) >= 2
    first_batch_paths = [c.path for c in groups[0]]
    assert "nexus/a.py" in first_batch_paths
    assert "nexus/b.py" in first_batch_paths


def test_behavior_preservation_verification_warning(tmp_path):
    """Refactor without test changes gets a warning."""
    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="nexus/monolith.py",
                reason="Refactor monolith",
                change_type=ChangeType.MODIFY,
            ),
            PlannedFileChange(
                path="nexus/extracted.py",
                reason="New extracted class",
                change_type=ChangeType.CREATE,
            ),
            # No test changes
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    assert any("test" in w.lower() for w in result.warnings)


def test_architecture_violation_detection(tmp_path):
    """Protected paths (e.g. SECURITY.md) cannot be changed without protected=True."""
    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="SECURITY.md",
                reason="Update security policy",
                change_type=ChangeType.MODIFY,
                protected=False,
            )
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    assert any("SECURITY" in v.path or "protected" in v.reason.lower() for v in result.scope_violations)
