from pathlib import Path

from nexus.verification import CheckStatus, CheckType, VerificationEngine


def _write_project(root: Path, *, add_new_failure: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        "[project]\nname='baseline-test'\nversion='0.0.0'\n"
        "[tool.pytest.ini_options]\ntestpaths=['.']\n",
        encoding="utf-8",
    )
    (root / "test_existing.py").write_text(
        "def test_existing_failure():\n    assert 1 == 2\n",
        encoding="utf-8",
    )
    if add_new_failure:
        (root / "test_new.py").write_text(
            "def test_new_regression():\n    assert 'new' == 'broken'\n",
            encoding="utf-8",
        )


def test_identical_preexisting_test_failure_is_marked_inherited(tmp_path):
    baseline = tmp_path / "baseline"
    workspace = tmp_path / "workspace"
    _write_project(baseline)
    _write_project(workspace)

    engine = VerificationEngine(str(workspace), {"test": "python -m pytest -q"}, allow_unisolated_host_process=True)
    report = engine.run_all([CheckType.TEST])
    assert report.all_passed is False

    reconciled = engine.reconcile_with_baseline(report, baseline)
    assert reconciled.all_passed is True
    assert reconciled.checks[0].status == CheckStatus.INHERITED
    assert "no new regression" in reconciled.checks[0].output


def test_changed_failure_output_remains_blocking_regression(tmp_path):
    baseline = tmp_path / "baseline"
    workspace = tmp_path / "workspace"
    _write_project(baseline)
    _write_project(workspace, add_new_failure=True)

    engine = VerificationEngine(str(workspace), {"test": "python -m pytest -q"}, allow_unisolated_host_process=True)
    report = engine.run_all([CheckType.TEST])
    reconciled = engine.reconcile_with_baseline(report, baseline)

    assert reconciled.all_passed is False
    assert reconciled.checks[0].status == CheckStatus.FAILED


def test_failure_normalization_ignores_terminal_and_runtime_noise(tmp_path):
    first_root = tmp_path / "pytest-of-user" / "pytest-41" / "workspace"
    second_root = tmp_path / "pytest-of-user" / "pytest-99" / "baseline"
    first = f"""F [100%]
=================================== FAILURES ===================================
FAILED test_existing.py::test_existing_failure - assert 1 == 2
1 failed in 0.04s
path={first_root}
address=0xABC123
"""
    second = f"""F [100%]
=================================== FAILURES ===================================
FAILED test_existing.py::test_existing_failure - assert 1 == 2
1 failed in 1.27s
path={second_root}
address=0xDEF456
"""

    normalized_first = VerificationEngine._normalize_failure_output(
        first, (first_root, second_root)
    )
    normalized_second = VerificationEngine._normalize_failure_output(
        second, (first_root, second_root)
    )

    assert normalized_first == normalized_second
