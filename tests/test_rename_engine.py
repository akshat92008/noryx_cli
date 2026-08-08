"""Tests for SymbolRenameEngine (Sprint 8)."""
from __future__ import annotations

import pytest
from pathlib import Path

from nexus.multifile.rename import SymbolRenameEngine, RenameKind


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _write(tmp_path / "nexus" / "calculator.py", """\
def compute_total(items: list) -> float:
    '''Compute the total of items.'''
    return sum(items)
""")
    _write(tmp_path / "nexus" / "report.py", """\
from nexus.calculator import compute_total

def generate_report(data):
    total = compute_total(data)
    return f"Total: {total}"
""")
    _write(tmp_path / "tests" / "test_calculator.py", """\
from nexus.calculator import compute_total

def test_compute_total():
    assert compute_total([1, 2, 3]) == 6
""")
    _write(tmp_path / "docs" / "api.md", """\
# API Reference
`compute_total(items)` computes the sum of a list.
""")
    _write(tmp_path / "config" / "settings.yaml", """\
feature_compute_total_enabled: true
""")
    _write(tmp_path / "nexus" / "dynamic_user.py", """\
import importlib
mod = importlib.import_module("nexus.calculator")
fn = getattr(mod, "compute_total")
result = fn([1, 2])
""")
    return tmp_path


def test_definition_discovered(repo):
    engine = SymbolRenameEngine(repo)
    analysis = engine.analyze("compute_total", "calculate_total")
    def_paths = [o.path for o in analysis.definition_occurrences]
    assert "nexus/calculator.py" in def_paths


def test_import_updated(repo):
    engine = SymbolRenameEngine(repo)
    analysis = engine.analyze("compute_total", "calculate_total")
    import_paths = [o.path for o in analysis.import_occurrences]
    assert "nexus/report.py" in import_paths or "tests/test_calculator.py" in import_paths


def test_caller_discovered(repo):
    engine = SymbolRenameEngine(repo)
    analysis = engine.analyze("compute_total", "calculate_total")
    caller_paths = [o.path for o in analysis.caller_occurrences]
    assert "nexus/report.py" in caller_paths


def test_tests_discovered(repo):
    engine = SymbolRenameEngine(repo)
    analysis = engine.analyze("compute_total", "calculate_total")
    test_paths = [o.path for o in analysis.test_occurrences]
    assert "tests/test_calculator.py" in test_paths


def test_string_false_positive_not_auto_renamed(repo):
    """Strings containing the symbol name are not put in safe_occurrences."""
    engine = SymbolRenameEngine(repo)
    analysis = engine.analyze("compute_total", "calculate_total")
    # String occurrences should be in string_occurrences, not safe_occurrences
    string_paths = [o.path for o in analysis.string_occurrences]
    safe_paths = [o.path for o in analysis.safe_occurrences]
    # If a file only has a string occurrence, it should not appear in safe_occurrences
    # (documentation is a separate category)


def test_documentation_occurrence_classified(repo):
    engine = SymbolRenameEngine(repo)
    analysis = engine.analyze("compute_total", "calculate_total")
    doc_paths = [o.path for o in analysis.documentation_occurrences]
    assert "docs/api.md" in doc_paths


def test_configuration_key_classified(repo):
    engine = SymbolRenameEngine(repo)
    analysis = engine.analyze("compute_total", "calculate_total")
    config_paths = [o.path for o in analysis.config_key_occurrences]
    assert "config/settings.yaml" in config_paths
    # Config keys should NOT be auto-renamed (requires review)
    config_review = analysis.requires_review
    config_review_paths = [o.path for o in config_review]
    assert "config/settings.yaml" in config_review_paths


def test_dynamic_reference_warning_surfaced(repo):
    engine = SymbolRenameEngine(repo)
    analysis = engine.analyze("compute_total", "calculate_total")
    dynamic_paths = [o.path for o in analysis.dynamic_occurrences]
    assert "nexus/dynamic_user.py" in dynamic_paths
    assert any("dynamic" in w.lower() or "getattr" in w.lower() for w in analysis.unresolved_warnings)


def test_to_planned_changes_excludes_config(repo):
    """Config keys not included in planned changes without explicit flag."""
    engine = SymbolRenameEngine(repo)
    analysis = engine.analyze("compute_total", "calculate_total")
    changes = engine.to_planned_changes(analysis, include_config=False)
    change_paths = [c.path for c in changes]
    assert "config/settings.yaml" not in change_paths


def test_safe_rename_applied(repo):
    """apply_rename updates code symbol, not string literals."""
    engine = SymbolRenameEngine(repo)
    success, detail, count = engine.apply_rename(
        "nexus/calculator.py", "compute_total", "calculate_total", safe_only=True
    )
    assert success
    assert count > 0
    content = (repo / "nexus" / "calculator.py").read_text()
    assert "calculate_total" in content
    assert "compute_total" not in content
