from __future__ import annotations

from types import SimpleNamespace

from nexus.agent import Agent, _effective_evidence
from nexus.tool_executor import ToolExecutionController
from nexus.tools import ToolResult, ToolStatus
from nexus.verification import CheckStatus, CheckType, VerificationEngine
from nexus.verification_evidence import (
    analyse_test_command,
    validate_test_execution,
)
from nexus.verification_evidence import (
    test_origin_for_profile as classify_test_origin,
)


def _test_record(
    record_id: str,
    *,
    status: str,
    scope: str,
    runner: str = "pytest",
    revision: str = "rev-1",
    targets: list[str] | None = None,
    project_gate: bool = False,
):
    return {
        "id": record_id,
        "kind": "verification_check",
        "status": status,
        "command": "pytest -q",
        "metadata": {
            "check_type": "test",
            "test_runner": runner,
            "verification_scope": scope,
            "test_targets": targets or [],
            "workspace_revision": revision,
            "project_gate": project_gate,
            "verification_valid": status == "verified",
        },
    }


def test_arbitrary_command_containing_pytest_is_not_test_evidence(tmp_path):
    profile = analyse_test_command("python -c \"print('pytest')\"", root=tmp_path)
    assert profile.valid is False
    valid, detail, count = validate_test_execution(profile, output="pytest\n", exit_code=0)
    assert valid is False and count is None
    assert "not" in detail.lower() or "recogn" in detail.lower()


def test_compound_shell_command_is_not_test_evidence(tmp_path):
    profile = analyse_test_command("pytest -q || true", root=tmp_path)
    assert profile.valid is False


def test_zero_test_success_is_rejected(tmp_path):
    profile = analyse_test_command("python -m unittest", root=tmp_path)
    valid, detail, count = validate_test_execution(
        profile, output="Ran 0 tests in 0.000s\n\nOK\n", exit_code=0
    )
    assert valid is False and count == 0 and "zero" in detail


def test_test_origin_requires_exact_planning_time_hash(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "test_api.py"
    test_file.write_text("def test_ok(): pass\n", encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(test_file.read_bytes()).hexdigest()
    profile = analyse_test_command("pytest -q tests/test_api.py", root=tmp_path)
    assert (
        classify_test_origin(profile, {"tests/test_api.py": digest}, root=tmp_path)
        == "pre_existing"
    )
    test_file.write_text("def test_ok(): assert True\n", encoding="utf-8")
    assert (
        classify_test_origin(profile, {"tests/test_api.py": digest}, root=tmp_path)
        == "modified_pre_existing"
    )


def test_generated_test_is_not_pre_existing(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_new.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    profile = analyse_test_command("pytest -q tests/test_new.py", root=tmp_path)
    assert classify_test_origin(profile, {}, root=tmp_path) == "model_generated"


def test_narrow_pass_never_supersedes_broad_failure():
    records = [
        _test_record("broad", status="failed", scope="full_suite", project_gate=True),
        _test_record(
            "narrow", status="verified", scope="targeted", targets=["tests/test_small.py"]
        ),
    ]
    assert {item["id"] for item in _effective_evidence(records, "verification_check")} == {
        "broad",
        "narrow",
    }


def test_broad_pass_covers_same_revision_targeted_failure():
    records = [
        _test_record("narrow", status="failed", scope="targeted", targets=["tests/test_small.py"]),
        _test_record("broad", status="verified", scope="full_suite", project_gate=True),
    ]
    assert [item["id"] for item in _effective_evidence(records, "verification_check")] == ["broad"]


def test_engineering_ledger_ignores_fake_pytest_substring(tmp_path):
    recorded: list[str] = []
    brain = SimpleNamespace(
        contract=SimpleNamespace(related_tests=["tests/test_api.py", "tests/test_db.py"]),
        record_verified_files=lambda paths: recorded.extend(paths),
    )
    controller = object.__new__(ToolExecutionController)
    controller._agent = SimpleNamespace(working_dir=str(tmp_path), engineering_brain=brain)
    controller._track_engineering_context(
        name="run_command",
        args={"command": "python -c \"print('pytest')\""},
        file_path="",
        result=ToolResult(ToolStatus.SUCCESS, "✅ $ python -c pass\npytest\n"),
        success=True,
    )
    assert recorded == []


def test_engineering_ledger_records_only_explicit_target(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_api.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    recorded: list[str] = []
    brain = SimpleNamespace(
        contract=SimpleNamespace(related_tests=["tests/test_api.py", "tests/test_db.py"]),
        record_verified_files=lambda paths: recorded.extend(paths),
    )
    controller = object.__new__(ToolExecutionController)
    controller._agent = SimpleNamespace(working_dir=str(tmp_path), engineering_brain=brain)
    controller._track_engineering_context(
        name="run_command",
        args={"command": "pytest -q tests/test_api.py"},
        file_path="",
        result=ToolResult(
            ToolStatus.SUCCESS, "✅ $ pytest -q tests/test_api.py\n1 passed in 0.01s\n"
        ),
        success=True,
    )
    assert recorded == ["tests/test_api.py"]


def test_model_declared_arbitrary_success_command_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    agent = Agent(
        api_key="test",
        working_dir=str(tmp_path),
        workspace_isolation=False,
        allow_unisolated_host_process=True,
    )
    output, verified, record_id = agent._run_declared_test_command(
        'python -c "pass"', source="model"
    )
    record = next(item for item in agent.evidence.records() if item["id"] == record_id)
    assert verified is False and "Rejected" in output
    assert record["status"] == "failed" and record["metadata"]["runner_valid"] is False


def test_verification_engine_rejects_zero_unittest_suite(tmp_path):
    engine = VerificationEngine(
        str(tmp_path),
        custom_commands={"test": "python -m unittest"},
        require_os_isolation=False,
        allow_unisolated_host_process=True,
    )
    result = engine.run_check(CheckType.TEST)
    assert result.status == CheckStatus.FAILED
    assert "zero tests" in result.output.lower() or "ran 0 tests" in result.output.lower()
