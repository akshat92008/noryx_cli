import os
from unittest.mock import MagicMock

import pytest

from nexus.agent import Agent
from nexus.cli import _close_and_exit
from nexus.doctor import _ISOLATION_REQUIRED_MODES
from nexus.pipeline import ExecutionPipeline
from nexus.policy import get_mode_policy
from nexus.sandbox import SandboxRunner


def test_sandbox_credentials_leak_prevention(tmp_path):
    sandbox = SandboxRunner(workspace=str(tmp_path))
    
    # Mock environment
    os.environ["NEXUS_API_KEY"] = "secret123"
    os.environ["NEXUS_TOKEN"] = "token123"
    os.environ["NEXUS_SECRET_SAUCE"] = "sauce123"
    os.environ["NEXUS_SAFE_VAR"] = "safe123"
    os.environ["PATH"] = "/bin:/usr/bin"
    os.environ["PYTHONPATH"] = "/usr/lib/python"
    os.environ["NODE_PATH"] = "/usr/lib/node"
    
    env = sandbox._filtered_env({})
    
    # Assert sensitive keys are rejected
    assert "NEXUS_API_KEY" not in env
    assert "NEXUS_TOKEN" not in env
    assert "NEXUS_SECRET_SAUCE" not in env
    
    # Assert safe key is kept
    assert "PATH" in env
    
    # Assert PYTHONPATH and NODE_PATH are not in the default filtered env
    assert "PYTHONPATH" not in env
    assert "NODE_PATH" not in env


def test_review_mode_policy():
    policy = get_mode_policy("review")
    assert policy.require_os_isolation is True
    assert "review" in _ISOLATION_REQUIRED_MODES


def test_quality_mode_distinct_reviewer():
    policy = get_mode_policy("quality")
    agent = Agent(api_key="dummy", model_key="custom", model_id_override="test_model", mode_policy=policy, working_dir="/tmp")
    agent.client = MagicMock()
    agent.client.id = "dummy_provider"
    
    # Set the same model for review and execution
    os.environ["NEXUS_REVIEW_MODEL_ID"] = "test_model"
    
    # Inject a mutation so it doesn't short circuit
    agent.evidence.append(kind="file_mutation", claim="did something", status="verified", raw_output="")
    agent.history = MagicMock()
    agent.history.changes = ["fake_change"]
    agent.history.get_recent_diffs.return_value = "fake diff"
    
    approved, message = agent._run_independent_review()
    
    assert approved is False
    assert "Independent review failed closed: quality mode requires a reviewer model different from the executor." in message


def test_pipeline_repo_understanding_path_fix(tmp_path):
    agent = Agent(api_key="dummy", working_dir=str(tmp_path))
    pipeline = ExecutionPipeline(agent)
    
    # This shouldn't raise a TypeError because working_dir is converted to a Path object
    result = pipeline._stage_repo_understanding()
    assert result.success is True


def test_cli_keep_workspace_default():
    # If keep-workspace is False (the default) and exit code is 0, the workspace should be discarded.
    agent = Agent(api_key="dummy", working_dir="/tmp")
    agent.keep_workspace = False
    agent.close = MagicMock()
    
    with pytest.raises(SystemExit) as excinfo:
        _close_and_exit(agent, 0)
        
    assert excinfo.value.code == 0
    agent.close.assert_called_once_with(discard_workspace=True)

def test_cli_keep_workspace_true():
    # If keep-workspace is True, the workspace should NOT be discarded even on success.
    agent = Agent(api_key="dummy", working_dir="/tmp")
    agent.keep_workspace = True
    agent.close = MagicMock()
    
    with pytest.raises(SystemExit) as excinfo:
        _close_and_exit(agent, 0)
        
    assert excinfo.value.code == 0
    agent.close.assert_called_once_with(discard_workspace=False)
