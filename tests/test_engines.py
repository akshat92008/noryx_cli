"""
Unit tests for the upgraded NexusAI Agent OS Engines (Phase 1 core layers).
Covers Planning, Reflection, Context Management, Safety, Project Memory, User Memory, and Verification.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nexus.context_manager import ContextManager
from nexus.planner import IntentType, PlanningEngine, PlanType, TaskStatus
from nexus.project_memory import ProjectMemory
from nexus.reflection import ReflectionEngine, ReflectionVerdict
from nexus.safety import SafetyLayer, SafetyLevel
from nexus.user_memory import UserMemory
from nexus.verification import CheckType, VerificationEngine


def test_planner_basic():
    """Test the planning engine intent classification and step generation."""
    planner = PlanningEngine()
    analysis = planner.analyze(
        "Build a comprehensive production-ready fullstack enterprise application from scratch with multiple databases and complex payment system architectures"
    )
    assert analysis["intent"] == IntentType.BUILD
    assert analysis["plan_type"] == PlanType.PLANNED

    plan = planner.create_plan(
        "Build a comprehensive production-ready fullstack enterprise application from scratch with multiple databases and complex payment system architectures",
        analysis,
    )
    assert plan is not None
    assert len(plan.steps) > 0
    assert plan.next_step is not None
    assert plan.progress == 0.0

    # Advance a step
    success = planner.advance_step(plan.steps[0].id, TaskStatus.COMPLETED, "Created base project")
    assert success
    assert plan.steps[0].status == TaskStatus.COMPLETED


def test_reflection_engine():
    """Test the reflection engine loop detection and error retry mechanics."""
    reflector = ReflectionEngine()

    # Simulate a successful tool call
    verdict1 = reflector.reflect(
        tool_name="write_file",
        tool_args={"path": "test.py", "content": "print('hello')"},
        tool_output="File written successfully",
    )
    assert verdict1.verdict == ReflectionVerdict.SUCCESS

    # Simulate repeated command failures to trigger loop detection
    for _ in range(5):
        verdict = reflector.reflect(
            tool_name="run_command",
            tool_args={"command": "python test.py"},
            tool_output="❌ Traceback (most recent call last):\nImportError: No module named pytest",
        )

    # Should flag as ESCALATE or RETRY after enough repeats
    assert verdict.verdict in (ReflectionVerdict.ESCALATE, ReflectionVerdict.RETRY)


def test_context_manager():
    """Test context tracking, file imports tracking, and architecture summarization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cm = ContextManager(working_dir=tmpdir)
        (Path(tmpdir) / "main.py").write_text("import os\n")

        # Test basic tracking
        cm.track_file_access("main.py", was_edited=True)
        assert str((Path(tmpdir) / "main.py").resolve()) in cm._file_contexts

        # Test imports parser
        cm.track_file_imports(
            "main.py", "import os\nfrom datetime import datetime\nimport requests"
        )
        relevant = cm.get_relevant_context("Help me edit main.py")
        assert "main.py" in relevant or "STRUCTURE" in relevant


def test_context_summaries_and_dependency_impact_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    module = workspace / "service.py"
    dependency = workspace / "domain.py"
    module.write_text("from domain import Entity\n\ndef execute():\n    return Entity()\n")
    dependency.write_text("class Entity:\n    pass\n")

    first = ContextManager(str(workspace))
    first.track_file_access("domain.py")
    first.track_file_access("service.py", was_edited=True)

    restored = ContextManager(str(workspace))
    relevant = restored.get_relevant_context("change execute in service.py")
    impact = restored.get_change_impact_context(["domain.py"])

    assert "functions: execute" in relevant
    assert "service.py" in impact
    assert "domain.py" in impact


def test_context_refresh_removes_stale_dependencies_and_deleted_files(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / "old_domain.py").write_text("class Old: pass\n")
    (workspace / "new_domain.py").write_text("class New: pass\n")
    service = workspace / "service.py"
    service.write_text("from old_domain import Old\n\ndef execute(): return Old()\n")

    manager = ContextManager(str(workspace))
    for path in ("old_domain.py", "new_domain.py", "service.py"):
        manager.track_file_access(path)
    service.write_text("from new_domain import New\n\ndef execute(): return New()\n")
    (workspace / "old_domain.py").unlink()

    new_impact = manager.get_change_impact_context(["new_domain.py"])
    context = manager.get_relevant_context("service execute")

    assert "service.py" in new_impact
    assert "old_domain.py" not in context
    assert "new_domain" in manager._file_contexts[str(service.resolve())].imports


def test_relative_python_and_javascript_imports_build_real_dependency_edges(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    workspace = tmp_path / "repo"
    package = workspace / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "domain.py").write_text("class Entity: pass\n")
    (package / "service.py").write_text("from .domain import Entity\n")
    (workspace / "domain.ts").write_text("export const value = 1\n")
    (workspace / "client.ts").write_text("import { value } from './domain'\n")

    manager = ContextManager(str(workspace))
    for path in ("pkg/domain.py", "pkg/service.py", "domain.ts", "client.ts"):
        manager.track_file_access(path)

    assert str((package / "service.py").resolve()) in manager.get_dependency_context(
        "pkg/domain.py"
    )
    assert str((workspace / "client.ts").resolve()) in manager.get_dependency_context(
        "domain.ts"
    )


def test_safety_layer():
    """Test command and file write validation in Safety Layer."""
    safety = SafetyLayer()

    # Safe command
    check1 = safety.check_command("ls -la")
    assert check1.is_allowed

    # Dangerous or suspicious commands
    check2 = safety.check_command("rm -rf /")
    assert not check2.is_allowed
    assert check2.level == SafetyLevel.BLOCKED

    # Secret checking in content
    secrets = safety.check_content_for_secrets(
        "Here is my key: nvapi-secret-key-12345-long-enough-credential"
    )
    assert len(secrets) > 0


def test_project_memory():
    """Test loading and parsing of project rules (NEXUS.md)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        pm = ProjectMemory(working_dir=tmpdir)

        # rules file doesn't exist yet
        assert not pm.rules_file_exists()

        # create default rules
        pm.create_default_rules()
        assert pm.rules_file_exists()

        rules = pm.load_rules()
        assert rules.build_command is not None
        assert rules.test_command is not None


def test_user_memory():
    """Test user memory profile customization and persistent habits."""
    um = UserMemory()
    um.reset()

    # Save a convention preference
    um.add_convention("Always write docstrings for Python classes")
    addon = um.get_prompt_addon()
    assert "Always write docstrings" in addon

    # Save liked/disliked details
    um.add_disliked_pattern("print statements for debugging")
    assert "print statements" in um.get_prompt_addon()


def test_verification_engine():
    """Test automated lint/test verification detection."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a mock requirements.txt or pytest config to detect project type
        (Path(tmpdir) / "requirements.txt").write_text("pytest\n")

        ve = VerificationEngine(working_dir=tmpdir)
        assert ve.project_type == "python"

        available = ve.get_available_checks()
        assert CheckType.TEST in available
