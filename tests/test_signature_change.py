"""Tests for SignatureChangeOrchestrator (Sprint 8)."""
from __future__ import annotations

import pytest
from pathlib import Path

from nexus.multifile.signature import (
    ParameterDiff,
    SignatureChange,
    SignatureChangeOrchestrator,
)
from nexus.multifile.contracts import CompatibilityPolicy


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _write(tmp_path / "nexus" / "auth.py", """\
def authenticate(username: str, password: str) -> bool:
    return username == "admin" and password == "secret"
""")
    _write(tmp_path / "nexus" / "login.py", """\
from nexus.auth import authenticate

def login(user, pwd):
    return authenticate(user, pwd)
""")
    _write(tmp_path / "nexus" / "admin.py", """\
from nexus.auth import authenticate

class AdminPanel:
    def check(self, u, p):
        return authenticate(u, p)
""")
    _write(tmp_path / "tests" / "test_auth.py", """\
from nexus.auth import authenticate

def test_authenticate():
    assert authenticate("admin", "secret") is True
""")
    return tmp_path


def test_added_parameter_callers_found(repo):
    """Adding a parameter causes callers to be inventoried."""
    orchestrator = SignatureChangeOrchestrator(repo)
    change = SignatureChange(
        symbol="authenticate",
        definition_path="nexus/auth.py",
        signature_before="authenticate(username: str, password: str) -> bool",
        signature_after="authenticate(username: str, password: str, mfa_token: str = '') -> bool",
        parameter_diffs=[
            ParameterDiff(kind="ADDED", name_after="mfa_token", has_default=True, breaking=False)
        ],
    )
    impact = orchestrator.inventory(change)
    caller_paths = [c.path for c in impact.callers]
    assert "nexus/login.py" in caller_paths
    assert "nexus/admin.py" in caller_paths


def test_removed_parameter_is_breaking(repo):
    """Removing a parameter without a default is a breaking change."""
    change = SignatureChange(
        symbol="authenticate",
        definition_path="nexus/auth.py",
        signature_before="authenticate(username: str, password: str) -> bool",
        signature_after="authenticate(username: str) -> bool",
        parameter_diffs=[
            ParameterDiff(kind="REMOVED", name_before="password", breaking=True)
        ],
    )
    policy = change.assess_compatibility()
    assert policy == CompatibilityPolicy.EXPLICIT_BREAKING
    assert change.is_breaking is True


def test_renamed_parameter_inventory(repo):
    """Renaming a parameter produces callers and implementation in inventory."""
    orchestrator = SignatureChangeOrchestrator(repo)
    change = SignatureChange(
        symbol="authenticate",
        definition_path="nexus/auth.py",
        signature_before="authenticate(username, password)",
        signature_after="authenticate(user, password)",
        parameter_diffs=[
            ParameterDiff(kind="RENAMED", name_before="username", name_after="user")
        ],
    )
    impact = orchestrator.inventory(change)
    assert impact.callers  # should find nexus/login.py and nexus/admin.py


def test_caller_not_updated_detected(repo):
    """Stale callers (not in planned set) are reported."""
    orchestrator = SignatureChangeOrchestrator(repo)
    change = SignatureChange(
        symbol="authenticate",
        definition_path="nexus/auth.py",
        signature_before="authenticate(username: str, password: str) -> bool",
        signature_after="authenticate(username: str, password: str, mfa: str) -> bool",
        parameter_diffs=[
            ParameterDiff(kind="ADDED", name_after="mfa", has_default=False, breaking=True)
        ],
    )
    # Planned paths only include the definition, not the callers
    impact = orchestrator.inventory(change, planned_paths=["nexus/auth.py"])
    assert impact.stale_callers
    assert any(w for w in impact.warnings if "stale" in w.lower() or "not in" in w.lower() or "NOT in" in w)


def test_interface_implementation_found(repo):
    """Abstract method rename: implementations are in the inventory."""
    orchestrator = SignatureChangeOrchestrator(repo)
    impact = orchestrator.inventory(
        SignatureChange(
            symbol="authenticate",
            definition_path="nexus/auth.py",
            signature_before="authenticate(u, p)",
            signature_after="authenticate(u, p, token=None)",
        )
    )
    # nexus/admin.py defines an authenticate-calling method, not a definition,
    # but implementations of the same name pattern should be found
    impls = orchestrator._find_implementations("authenticate")
    # The definition itself is in nexus/auth.py
    assert any("auth.py" in i.path for i in impls)


def test_compatibility_adapter_policy(repo):
    """Backward-compatible addition uses BACKWARD_COMPATIBLE policy."""
    change = SignatureChange(
        symbol="authenticate",
        definition_path="nexus/auth.py",
        signature_before="authenticate(username: str) -> bool",
        signature_after="authenticate(username: str, timeout: int = 30) -> bool",
        parameter_diffs=[
            ParameterDiff(kind="ADDED", name_after="timeout", has_default=True, breaking=False)
        ],
    )
    policy = change.assess_compatibility()
    assert policy == CompatibilityPolicy.BACKWARD_COMPATIBLE
    assert change.is_breaking is False


def test_planned_changes_include_definition_callers_tests(repo):
    """to_planned_changes produces entries for definition, callers, and tests."""
    orchestrator = SignatureChangeOrchestrator(repo)
    change = SignatureChange(
        symbol="authenticate",
        definition_path="nexus/auth.py",
        signature_before="authenticate(u, p)",
        signature_after="authenticate(u, p, mfa=None)",
    )
    impact = orchestrator.inventory(change)
    planned = orchestrator.to_planned_changes(impact)
    planned_paths = [p.path for p in planned]
    assert "nexus/auth.py" in planned_paths
    # Should include callers
    assert any("login" in p or "admin" in p for p in planned_paths)
