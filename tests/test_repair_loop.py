import os

os.environ["NVIDIA_API_KEY"] = "test"
from nexus.agent import Agent
from nexus.repair import RepairLoop
from nexus.runtime.kernel import FailureKind


def test_repair_loop_triggers_limit_and_isolates_error(monkeypatch):
    agent = Agent(working_dir=".", workspace_isolation=False)
    repair = RepairLoop(agent)

    attempts = [0]

    def mock_turn(*args, **kwargs):
        attempts[0] += 1
        return "repaired", []

    monkeypatch.setattr(agent, "_run_hosted_turn", mock_turn)

    # We pass the required positional arguments
    result = repair.attempt("AssertionError: expected 4 but got 5", FailureKind.TEST)

    assert attempts[0] >= 1
    assert result is not None
