"""Tests for ImpactAnalyzer (Sprint 8)."""
from __future__ import annotations

import pytest
from pathlib import Path

from nexus.multifile.contracts import (
    ContractChange,
    ContractScope,
    ContractType,
    ImpactCategory,
    SymbolReference,
)
from nexus.multifile.impact import ImpactAnalyzer


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A minimal in-memory repo with a definition and callers."""
    _write(tmp_path / "nexus" / "api.py", """\
def get_data(user_id: str) -> dict:
    return {}
""")
    _write(tmp_path / "nexus" / "service.py", """\
from nexus.api import get_data

def process(uid):
    return get_data(uid)
""")
    _write(tmp_path / "nexus" / "other.py", """\
# No reference to get_data
def foo(): pass
""")
    _write(tmp_path / "tests" / "test_api.py", """\
from nexus.api import get_data

def test_get_data():
    assert get_data("u1") == {}
""")
    return tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_function_signature_change_impact(repo):
    """Direct callers and tests are discovered for a signature change."""
    analyzer = ImpactAnalyzer(repo_root=repo)
    cc = ContractChange(
        contract_id="cc-get-data",
        contract_type=ContractType.PUBLIC_FUNCTION,
        definition=SymbolReference(path="nexus/api.py", symbol="get_data"),
        current_contract="get_data(user_id: str)",
        proposed_contract="get_data(user_id: str, timeout: int = 30)",
        scope=ContractScope.REPOSITORY_PUBLIC,
    )
    report = analyzer.analyze([cc])

    directly_paths = [t.path for t in report.directly_affected]
    assert "nexus/service.py" in directly_paths
    # other.py should NOT appear
    assert "nexus/other.py" not in directly_paths


def test_reverse_imports_found(repo):
    """Files importing the module are discovered as transitively affected."""
    analyzer = ImpactAnalyzer(repo_root=repo)
    reverse = analyzer.discover_reverse_imports("nexus/api.py")
    reverse_paths = [t.path for t in reverse]
    assert "nexus/service.py" in reverse_paths
    assert "tests/test_api.py" in reverse_paths


def test_test_coverage_discovered(repo):
    """Test files covering the changed source are included in tests_required."""
    analyzer = ImpactAnalyzer(repo_root=repo)
    tests = analyzer.discover_test_coverage("nexus/api.py")
    test_paths = [t.path for t in tests]
    assert "tests/test_api.py" in test_paths


def test_configuration_dependency_found(repo):
    """Config files referencing a key are found."""
    (repo / "config.yaml").write_text("api_timeout: 30\n", encoding="utf-8")
    analyzer = ImpactAnalyzer(repo_root=repo)
    refs = analyzer.discover_configuration_references("api_timeout")
    assert any("config.yaml" in t.path for t in refs)


def test_source_to_test_relationship(repo):
    """Source-to-test mapping produces TestTarget entries."""
    analyzer = ImpactAnalyzer(repo_root=repo)
    cc = ContractChange(
        contract_id="cc-get-data",
        contract_type=ContractType.PUBLIC_FUNCTION,
        definition=SymbolReference(path="nexus/api.py", symbol="get_data"),
        current_contract="get_data()",
        proposed_contract="get_data(timeout: int)",
        scope=ContractScope.REPOSITORY_PUBLIC,
    )
    report = analyzer.analyze([cc])
    assert report.tests_required


def test_unresolved_dynamic_caller_surfaced(repo):
    """Dynamic references are surfaced in unresolved_dynamic_dependencies."""
    _write(repo / "nexus" / "dynamic.py", """\
import importlib
mod = importlib.import_module("nexus.api")
fn = getattr(mod, "get_data")
""")
    analyzer = ImpactAnalyzer(repo_root=repo)
    callers = analyzer.discover_callers("get_data", definition_path="nexus/api.py")
    dynamic = [c for c in callers if c.dynamic]
    assert dynamic, "Dynamic reference to get_data was not detected"
    assert any(c.category == ImpactCategory.UNRESOLVED for c in dynamic)


def test_monorepo_package_boundary(tmp_path):
    """Impact analysis respects package boundaries."""
    pkg_a = tmp_path / "packages" / "pkg_a" / "service.py"
    pkg_a.parent.mkdir(parents=True)
    pkg_a.write_text("def helper(): pass\n", encoding="utf-8")

    pkg_b = tmp_path / "packages" / "pkg_b" / "caller.py"
    pkg_b.parent.mkdir(parents=True)
    pkg_b.write_text("from packages.pkg_a.service import helper\n\nresult = helper()\n", encoding="utf-8")

    analyzer = ImpactAnalyzer(repo_root=tmp_path)
    callers = analyzer.discover_callers("helper", definition_path="packages/pkg_a/service.py")
    caller_paths = [c.path for c in callers]
    assert "packages/pkg_b/caller.py" in caller_paths


def test_external_api_contract_has_architecture_risk(repo):
    """External API contract changes produce architecture risk entries."""
    analyzer = ImpactAnalyzer(repo_root=repo)
    cc = ContractChange(
        contract_id="cc-external",
        contract_type=ContractType.API_ENDPOINT,
        definition=SymbolReference(path="nexus/api.py", symbol="external_endpoint"),
        current_contract="GET /v1/data",
        proposed_contract="GET /v2/data",
        scope=ContractScope.EXTERNAL_API,
    )
    report = analyzer.analyze([cc])
    assert report.architecture_risks, "External API change should generate architecture risk"
    assert report.unresolved_dynamic_dependencies
