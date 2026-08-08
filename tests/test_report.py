import json
from pathlib import Path

from nexus.report import FinalReportGenerator


def test_report_generation(tmp_path: Path):
    result_data = {
        "status": "VERIFIED",
        "objective": "Fix the bug in the code",
        "acceptance_criteria": [
            {"description": "Code compiles", "status": "VERIFIED"},
            {"description": "Tests pass", "status": "VERIFIED"},
        ],
        "work_completed": ["Fixed typo in main.py"],
        "files_changed": ["main.py"],
        "checks": [{"name": "pytest", "success": True}],
        "checks_skipped": ["eslint"],
        "costs": {"estimated_cost_usd": 0.15},
        "model_providers": ["glm-5.2"],
        "network_calls": ["github.com"],
        "permissions_used": ["write_file"],
        "remaining_risks": ["Might break edge case X"],
        "assumptions": ["User is on Linux"],
    }

    input_file = tmp_path / "final_report.json"
    input_file.write_text(json.dumps(result_data))

    report_text = FinalReportGenerator.generate(input_file)

    assert "Nexus Run Report" in report_text
    assert "VERIFIED" in report_text
    assert "Fix the bug in the code" in report_text
    assert "✅ Code compiles" in report_text
    assert "Fixed typo in main.py" in report_text
    assert "`main.py`" in report_text
    assert "✅ pytest" in report_text
    assert "⚠️ eslint" in report_text
    assert "$0.1500" in report_text
    assert "glm-5.2" in report_text
    assert "1" in report_text  # 1 network call
    assert "write_file" in report_text
    assert "Might break edge case X" in report_text
    assert "Assumption: User is on Linux" in report_text


def test_report_invalid_json(tmp_path: Path):
    input_file = tmp_path / "final_report.json"
    input_file.write_text("{invalid")

    report_text = FinalReportGenerator.generate(input_file)
    assert "Invalid JSON" in report_text


def test_report_missing_file(tmp_path: Path):
    report_text = FinalReportGenerator.generate(tmp_path / "missing.json")
    assert "No final report found" in report_text
