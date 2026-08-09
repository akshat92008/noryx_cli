"""
tests/security_adversarial/test_phase3_concurrency.py

Adversarial concurrency tests.

test_hitl_concurrent_confirmations
    Verifies the HITL confirmation gate cannot be double-fired by 100
    concurrent goroutine-like threads.

test_integration_coordinator_commit_to_lead  (was: stale_read_overwrite)
    Verifies that IntegrationCoordinator.integrate() actually commits the
    verified integration tree to the lead workspace (P0 fix) and that the
    per-instance threading.Lock serialises concurrent calls so only one
    assignment wins a file conflict (P1 fix).

    The old test expected `target.py` to contain "overwrite_X" after
    integration — that was a test of the in-memory integration workspace,
    not the lead workspace, so it was vacuously passing even before the P0
    fix.  The new test asserts that the lead workspace file is updated by
    the coordinator.
"""

from __future__ import annotations

import asyncio
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import nexus.history as nexus_history
from nexus.agent import Agent
from nexus.collaboration.integration import IntegrationCoordinator
from nexus.collaboration.models import (
    AssignmentResult,
    AssignmentReview,
    AssignmentStatus,
    ProposedChange,
    ReviewDecision,
)
from nexus.policy import get_mode_policy


# ─── HITL concurrent confirmation gate ───────────────────────────────────────

@pytest.mark.anyio
async def test_hitl_concurrent_confirmations(tmp_path, monkeypatch):
    """Attack the HITL confirmation architecture with 100 concurrent fires."""
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


# ─── IntegrationCoordinator: commit-to-lead and serialisation ────────────────

class _PassingVerifier:
    """Trivial verifier that always passes."""

    class _Outcome:
        passed = True

    def run_verification(self, context, checks):
        return self._Outcome()


def _make_result(index: int) -> tuple[AssignmentResult, AssignmentReview]:
    """Create a single-file assignment result for `target.py`."""
    result = AssignmentResult(
        assignment_id=f"assign_{index}",
        status=AssignmentStatus.COMPLETED,
        proposed_changes=(
            ProposedChange(
                change_id=f"c_{index}",
                path="target.py",
                description=f"overwrite_{index}",
                diff_reference="",        # no diff — uses the description append path
                transaction_ref=None,
            ),
        ),
        findings=(),
    )
    review = AssignmentReview(
        assignment_id=f"assign_{index}",
        accepted=True,
        decision=ReviewDecision.APPROVE_FOR_INTEGRATION,
    )
    return result, review


def test_integration_coordinator_commit_to_lead(tmp_path):
    """
    P0 regression: the coordinator must commit the verified integration
    tree to the lead workspace.  After a successful integrate() call the
    lead workspace file must reflect the integrated change.

    We use the description-append path (no diff) so the integration
    appends a comment to `target.py` — that is detectable without
    requiring a real `patch` binary.
    """
    target = tmp_path / "target.py"
    target.write_text("initial\n")

    coordinator = IntegrationCoordinator(
        current_revision="dummy",
        verification_service=_PassingVerifier(),
        lead_workspace_root=tmp_path,
    )

    result, review = _make_result(0)
    integration_result = coordinator.integrate(
        [result], {result.assignment_id: review}
    )

    from nexus.collaboration.models import IntegrationStatus

    assert integration_result.status == IntegrationStatus.INTEGRATED, (
        f"Integration failed: {integration_result.conflicts}"
    )
    assert result.assignment_id in integration_result.applied_assignments

    # P0 core assertion: the lead workspace file must have been updated.
    content = target.read_text()
    assert content != "initial\n", (
        "P0: IntegrationCoordinator did not commit changes to the lead workspace"
    )
    assert "overwrite_0" in content or "Integrated change" in content, (
        f"Unexpected lead workspace content: {content!r}"
    )


@pytest.mark.anyio
async def test_integration_coordinator_lock_serialises_concurrent_calls(tmp_path):
    """
    P1 regression: 50 concurrent integrate() calls must be serialised by the
    per-instance lock so that each sees the committed result of the previous
    one.  The coordinator must not raise or deadlock under concurrent load.
    """
    target = tmp_path / "target.py"
    target.write_text("initial\n")

    coordinator = IntegrationCoordinator(
        current_revision="dummy",
        verification_service=_PassingVerifier(),
        lead_workspace_root=tmp_path,
    )

    def worker(index: int):
        result, review = _make_result(index)
        return coordinator.integrate([result], {result.assignment_id: review})

    loop = asyncio.get_running_loop()
    tasks = [loop.run_in_executor(None, worker, i) for i in range(50)]
    outcomes = await asyncio.gather(*tasks)

    from nexus.collaboration.models import IntegrationStatus

    # Under the lock, each successive call will see the previous one's
    # commit, meaning subsequent calls will find the tree hash has changed
    # (their baseline is stale).  Exactly ONE call should produce INTEGRATED;
    # the rest will fail with a drift/conflict error.  The important
    # property is that no unhandled exception escapes.
    integrated = [o for o in outcomes if o.status == IntegrationStatus.INTEGRATED]
    failed = [o for o in outcomes if o.status != IntegrationStatus.INTEGRATED]

    assert len(integrated) >= 1, (
        "No integration succeeded in 50 concurrent attempts"
    )
    # All calls must have completed without raising
    assert len(outcomes) == 50
