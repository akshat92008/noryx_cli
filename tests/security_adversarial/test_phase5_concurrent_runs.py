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
    monkeypatch.setenv("NORYX_HOME", str(tmp_path / "noryx_home"))
    
    agent = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))
    agent.client.chat_sync = mock_chat_sync_slow
    
    result1 = []
    result2 = []
    
    def run_1():
        try:
            res = agent.run_non_interactive("task 1")
            result1.append(res)
        except Exception as e:
            result1.append(e)

    def run_2():
        try:
            res = agent.run_non_interactive("task 2")
            result2.append(res)
        except Exception as e:
            result2.append(e)

    t1 = threading.Thread(target=run_1)
    t2 = threading.Thread(target=run_2)
    
    t1.start()
    time.sleep(0.1) # Let t1 enter RUNNING state
    t2.start()
    
    t1.join()
    t2.join()
    
    # One of them should have failed or been rejected, because we cannot run twice concurrently on the same agent instance!
    # Or, the state machine should detect "already running".
    # We will print the results to see if they both ran successfully, which would be a bug.
    
    if isinstance(result1[0], Exception):
        print(f"Run 1 failed: {result1[0]}")
    if isinstance(result2[0], Exception):
        print(f"Run 2 failed: {result2[0]}")
    
    # Assertion depends on what we expect. Let's just run and see.
    # We expect one to raise a ValueError "already running" or similar.
    assert isinstance(result2[0], Exception) or isinstance(result1[0], Exception)
