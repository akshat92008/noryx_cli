import pytest
from nexus.agent.core import Agent
from nexus.run_state import RunStatus, RunLedger

def test_resume_failed_run(tmp_path, monkeypatch):
    monkeypatch.setenv("NORYX_HOME", str(tmp_path / "noryx_home"))
    
    agent = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))
    # We must properly initialize the ledger so it has a valid request/plan.
    # We must properly initialize the ledger so it has a valid request/plan.
    agent.run_id = "test-run-123"
    agent.conversation_id = agent.run_id
    agent.run_ledger = RunLedger(agent.run_id, str(tmp_path))
    agent.run_ledger.begin("hello")
    agent.run_ledger.record_plan({"id": "plan-123", "goal": "test goal", "steps": []})
    
    run_id = agent.run_id
    
    agent.run_ledger.finalize(RunStatus.FAILED, objective="test")
    
    # Try resuming a failed run
    agent2 = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))
    try:
        agent2.resume_interrupted(run_id)
        assert False, "Should not resume a failed run!"
    except ValueError as e:
        assert "terminal" in str(e).lower()
