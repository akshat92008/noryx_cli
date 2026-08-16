import pytest
import threading
import time
from unittest.mock import MagicMock
from nexus.agent.core import Agent

def mock_chat_sync_slow(*args, **kwargs):
    time.sleep(1.0)
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "All done!"
    mock_resp.choices[0].message.tool_calls = None
    mock_resp.usage.prompt_tokens = 10
    mock_resp.usage.completion_tokens = 10
    return mock_resp

def test_prevent_concurrent_runs(tmp_path, monkeypatch):
    """One Agent instance must reject overlapping turns deterministically."""
    from types import SimpleNamespace
    from nexus.pipeline import ExecutionPipeline

    monkeypatch.setenv("NORYX_HOME", str(tmp_path / "noryx_home"))

    def slow_pipeline_run(self, *_args, **_kwargs):
        time.sleep(0.5)
        return SimpleNamespace(response="All done!", events=[])

    monkeypatch.setattr(ExecutionPipeline, "run", slow_pipeline_run)
    agent = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))

    result1 = []
    result2 = []

    def run_1():
        try:
            result1.append(agent.run_non_interactive("task 1"))
        except Exception as exc:
            result1.append(exc)

    def run_2():
        try:
            result2.append(agent.run_non_interactive("task 2"))
        except Exception as exc:
            result2.append(exc)

    t1 = threading.Thread(target=run_1)
    t2 = threading.Thread(target=run_2)
    t1.start()
    time.sleep(0.1)
    t2.start()
    t1.join()
    t2.join()

    rejected = [item for item in (*result1, *result2) if isinstance(item, Exception)]
    assert len(rejected) == 1
    assert "already running" in str(rejected[0]).lower()
