import pytest
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

from nexus.agent.core import Agent
from nexus.planner import ExecutionPlan, PlanStep, IntentType, Difficulty, PlanType, TaskStatus
from nexus.pipeline import ExecutionPipeline


class MockPopen:
    def __init__(self, *args, **kwargs):
        raise OSError("Injected Subprocess Failure")

def test_subprocess_failure_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("NORYX_HOME", str(tmp_path / "noryx_home"))
    
    agent = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))
    
    # Inject MockPopen into nexus.sandbox
    import nexus.sandbox
    monkeypatch.setattr(nexus.sandbox.subprocess, "Popen", MockPopen)
    
    # We will use the agent to run a shell command.
    from nexus.tools.tools_impl import tool_run_command
    
    # Normally, the sandbox runner would execute this and capture output.
    # With MockPopen, it should throw OSError which is caught by SandboxRunner.run
    
    # Let's run it manually first to ensure it's caught
    result = tool_run_command("echo hello", cwd=str(tmp_path), require_os_isolation=False, allow_unisolated_host_process=True)
    
    print(f"Subprocess Failure Result: {result}")
    assert "❌ (exit code None)" in result or "❌ Error running command:" in result or "Injected Subprocess Failure" in result, f"Expected error in result, got: {result}"
