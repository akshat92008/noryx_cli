"""Tests for agent orchestration coverage."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nexus.agent import Agent
from nexus.run_state import RunLedger


def test_export_final_report_success(tmp_path: Path):
    agent = Agent(api_key="test", working_dir=str(tmp_path))
    agent.run_ledger = RunLedger("test-session-1", working_dir=str(tmp_path))
    # create dummy turn
    turn_dir = agent.run_ledger.session_dir / "turn-0001"
    turn_dir.mkdir(parents=True, exist_ok=True)
    report_file = turn_dir / "final_report.json"
    report_file.write_text(json.dumps({"status": "VERIFIED", "objective": "test"}))

    report = agent.export_final_report()
    assert report["status"] == "VERIFIED"
    assert report["objective"] == "test"


def test_export_final_report_no_turn(tmp_path: Path):
    agent = Agent(api_key="test", working_dir=str(tmp_path))
    agent.run_ledger = RunLedger("test-session-2", working_dir=str(tmp_path))

    report = agent.export_final_report()
    assert report["status"] == "UNVERIFIED"
    assert "No run data found" in report["error"]


def test_export_final_report_missing_file(tmp_path: Path):
    agent = Agent(api_key="test", working_dir=str(tmp_path))
    agent.run_ledger = RunLedger("test-session-3", working_dir=str(tmp_path))
    turn_dir = agent.run_ledger.session_dir / "turn-0001"
    turn_dir.mkdir(parents=True, exist_ok=True)

    report = agent.export_final_report()
    assert report["status"] == "UNVERIFIED"
    assert "No final report generated" in report["error"]


def test_run_interactive_interrupt(tmp_path: Path):
    agent = Agent(api_key="test", working_dir=str(tmp_path))
    agent.run_ledger = RunLedger("test-session-4", working_dir=str(tmp_path))

    # Mock planner to raise KeyboardInterrupt
    agent.planner = MagicMock()
    agent.planner.analyze.side_effect = KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        agent.run_non_interactive("test")
