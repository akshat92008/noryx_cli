from pathlib import Path

from nexus.architecture_health import run_architecture_health, scan_source_secrets


def test_architecture_health_passes_for_source_tree():
    report = run_architecture_health()
    assert report.passed, report.failures
    assert report.package_modules == report.imported_modules


def test_secret_scan_ignores_variable_names_but_detects_material(tmp_path: Path):
    (tmp_path / "nexus").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "nexus" / "safe.py").write_text("NVIDIA_API_KEY = getenv('NVIDIA_API_KEY')\n")
    passed, findings = scan_source_secrets(tmp_path)
    assert passed
    assert not findings

    (tmp_path / "scripts" / "bad.py").write_text("token='sk-" + "a" * 30 + "'\n")
    passed, findings = scan_source_secrets(tmp_path)
    assert not passed
    assert findings == ("scripts/bad.py",)


def test_architecture_cli_json(monkeypatch, capsys):
    import json
    import sys

    from nexus.cli.cli_impl import main

    monkeypatch.setattr(sys, "argv", ["nexus", "architecture", "check", "--json"])
    main()
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert payload["package_modules"] == payload["imported_modules"]


def test_source_layout_rejects_duplicate_top_level_definitions(tmp_path):
    from nexus.architecture_health import _check_source_layout

    package = tmp_path / "nexus"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "duplicate.py").write_text(
        "def run():\n    return 1\n\ndef run():\n    return 2\n",
        encoding="utf-8",
    )
    report = _check_source_layout(tmp_path)
    assert not report.passed
    assert any("duplicate top-level definition" in item for item in report.failures)
