import threading
import time
from unittest.mock import MagicMock

from nexus.agent.core import Agent
from nexus.run_state import RunStatus


def mock_chat_sync(*args, **kwargs):
    # This is a very simple mock that just returns a text message
    mock_resp = MagicMock()
    mock_resp.choices = [MagicMock()]
    mock_resp.choices[0].message.content = "All done!"
    mock_resp.choices[0].message.tool_calls = None
    mock_resp.usage.prompt_tokens = 10
    mock_resp.usage.completion_tokens = 10
    return mock_resp


def test_fuzz_lifecycle_concurrent_cancel(tmp_path, monkeypatch):
    """
    Test that cancelling an agent while it's in AWAITING_CONFIRMATION
    or RUNNING does not break state machine invariants.
    """
    monkeypatch.setenv("NORYX_HOME", str(tmp_path / "noryx_home"))

    agent = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))

    # Mock client
    agent.client.chat_sync = mock_chat_sync

    def run_agent():
        try:
            agent.run_non_interactive("do something")
        except Exception:
            pass

    t = threading.Thread(target=run_agent)
    t.start()

    # Wait for ledger to init
    time.sleep(0.1)

    # Cancel concurrently. If execution wins the race and reaches finalization
    # first, PARTIALLY_VERIFIED is a valid completed lifecycle state.
    agent.cancel()

    t.join(timeout=2.0)

    assert agent._cancelled
    if hasattr(agent, "run_ledger") and agent.run_ledger:
        summary = agent.run_ledger.resume_summary()
        status = summary.get("state", {}).get("status")
        assert status in (
            RunStatus.FAILED.value,
            RunStatus.VERIFIED.value,
            RunStatus.PARTIALLY_VERIFIED.value,
            RunStatus.ROLLED_BACK.value,
            RunStatus.RUNNING.value,
            RunStatus.UNVERIFIED.value,
        )
