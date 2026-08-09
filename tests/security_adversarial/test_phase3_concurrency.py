import asyncio
import pytest
from unittest.mock import MagicMock
from nexus.agent import Agent
from nexus.policy import get_mode_policy
import nexus.history as nexus_history
from nexus.collaboration.integration import IntegrationCoordinator
from nexus.collaboration.models import AssignmentResult, AssignmentReview, AssignmentStatus, ReviewDecision, ProposedChange
import tempfile
import uuid
import os
from pathlib import Path

@pytest.mark.anyio
async def test_hitl_concurrent_confirmations(tmp_path, monkeypatch):
    """Attack the newly fixed confirmation architecture."""
    monkeypatch.setattr(nexus_history, "HISTORY_DIR", tmp_path / ".nexusai" / "history")
    agent = Agent(model_key="nova3b", working_dir=str(tmp_path))
    agent._execute_tool_with_safety = MagicMock(return_value=("Success", True))
    
    class DummySafety:
        reason = "Test"
        details = "Test Details"
    conf_id = agent._queue_confirmation("test_tool", {"arg": "val"}, DummySafety())
    
    async def confirm_task():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, agent.confirm_pending_operation, conf_id)
        
    tasks = [confirm_task() for _ in range(100)]
    results = await asyncio.gather(*tasks)
    
    successes = [r for r in results if "Unknown or expired" not in r[0]]
    assert len(successes) == 1, f"A gated operation executed {len(successes)} times!"
    assert agent._execute_tool_with_safety.call_count == 1


@pytest.mark.anyio
async def test_integration_coordinator_stale_read_overwrite(tmp_path):
    """
    Test IntegrationCoordinator under concurrent load with same assignment IDs or conflicting changes.
    """
    (tmp_path / "target.py").write_text("initial\n")
    class DummyOutcome:
        passed = True

    class DummyVerifier:
        def run_verification(self, context, checks):
            return DummyOutcome()

    coordinator = IntegrationCoordinator(
        current_revision="dummy",
        verification_service=DummyVerifier(),
        lead_workspace_root=tmp_path
    )
    
    # 50 simulated workers all trying to overwrite "target.py"
    # Wait, integration is synchronous currently? 
    # Let's run it in executors.
    def worker_submission(index):
        result = AssignmentResult(
            assignment_id=f"assign_{index}",
            status=AssignmentStatus.COMPLETED,
            proposed_changes=(ProposedChange(
                change_id=f"c_{index}",
                path="target.py",
                description="test",
                diff_reference=f"-initial\n+overwrite_{index}\n",
                transaction_ref=None
            ),),
            findings=(),
        )
        review = AssignmentReview(
            assignment_id=f"assign_{index}",
            accepted=True,
            decision=ReviewDecision.APPROVE_FOR_INTEGRATION
        )
        return coordinator.integrate([result], {f"assign_{index}": review})

    loop = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, worker_submission, i) for i in range(50)]
    results = await asyncio.gather(*tasks)
    
    # Noryx must serialize these safely. 
    # The file should contain EXACTLY ONE overwrite.
    content = (tmp_path / "target.py").read_text()
    lines = content.strip().split("\n")
    assert len(lines) == 1, f"File was corrupted by concurrent overwrites: {content}"
    assert lines[0].startswith("overwrite_")
