import pytest
from pathlib import Path
from unittest.mock import MagicMock

from nexus.collaboration.integration import IntegrationCoordinator
from nexus.collaboration.lead_orchestrator import LeadOrchestrator
from nexus.collaboration.models import (
    CollaborationPolicyProfile,
    WorkerResult,
    WorkerResultStatus,
    WorkerReview,
    CollaborationState,
    AgentAssignment,
    MutationPolicy,
)

class AsyncMock(MagicMock):
    async def __call__(self, *args, **kwargs):
        return super(AsyncMock, self).__call__(*args, **kwargs)

def test_integration_coordinator_rejects_missing_verifier():
    """Verify that absent verifier produces VERIFICATION_UNAVAILABLE, not a stub pass."""
    coordinator = IntegrationCoordinator(current_revision="HEAD", verification_service=None)

    mock_result = WorkerResult(
        assignment_id="test-assign-1",
        worker_id="worker-1",
        status=WorkerResultStatus.COMPLETED,
        summary="done",
        findings=(),
        proposed_changes=(),
        transaction_reference=None,
        verification_results=(),
        unresolved_questions=(),
        risks=(),
        evidence_ids=("ev-1",),
        cost=MagicMock(),
    )

    mock_review = WorkerReview(
        assignment_id="test-assign-1",
        accepted=True,
        findings=(),
        missing_evidence=(),
        required_revisions=(),
        integration_eligible=True,
    )

    result = coordinator.integrate(
        accepted_results=[mock_result],
        reviews={"test-assign-1": mock_review},
    )

    # Should not contain STUB_PASS
    assert not any("STUB_PASS" in res for res in result.verification_results), "STUB_PASS was found in verification results"
    # Should contain an error or unavailable state
    assert any("VERIFICATION_UNAVAILABLE" in res for res in result.verification_results) or \
           any("ERROR" in res for res in result.verification_results), "Did not fail closed on missing verifier"


import asyncio

def test_lead_orchestrator_fails_on_stub_pass(tmp_path: Path):
    """Verify that the orchestrator does not accept STUB_PASS as a success."""
    
    async def run_test():
        policy = CollaborationPolicyProfile.CONTROLLED_PARALLEL
        
        # We pass a None verification_service to trigger the fallback logic
        orchestrator = LeadOrchestrator(
            run_id="run-1",
            policy=policy,
            lead_workspace_root=tmp_path / "workspace",
            current_revision="HEAD",
            persistence_dir=tmp_path / "persist",
            verification_service=None,
        )

        # Mock the lifecycle/runtime so we don't actually spawn real workers
        orchestrator._worker_runtime.execute = AsyncMock()
        
        mock_worker_record = MagicMock()
        mock_worker_record.worker_id = "w-1"
        orchestrator._lifecycle.create_worker = MagicMock(return_value=mock_worker_record)
        orchestrator._lifecycle.prepare_workspace = MagicMock()
        orchestrator._partitioner.build_packet = MagicMock()
        
        # Simulate an assignment
        assignment = AgentAssignment(
            assignment_id="test-assign-1",
            parent_run_id="run-1",
            role=MagicMock(),
            objective="Test",
            scope=MagicMock(),
            allowed_paths=(Path("."),),
            prohibited_paths=(),
            relevant_symbols=(),
            requirements=(),
            expected_outputs=(),
            verification_requirements=(),
            dependencies=(),
            mutation_policy=MutationPolicy(allowed=True),
            model_constraints=MagicMock(),
            budget=MagicMock(),
            deadline_seconds=60,
        )

        # Set up the mock state machine response
        async def mock_execute(*args, **kwargs):
            return WorkerResult(
                assignment_id="test-assign-1",
                worker_id="w-1",
                status=WorkerResultStatus.COMPLETED,
                summary="done",
                findings=(),
                proposed_changes=(),
                transaction_reference=None,
                verification_results=(),
                unresolved_questions=(),
                risks=(),
                evidence_ids=("ev-1",),
                cost=MagicMock(),
            )
        orchestrator._worker_runtime.execute.side_effect = mock_execute

        # Set up the reviewer to always accept
        orchestrator._reviewer.review = MagicMock(return_value=WorkerReview(
            assignment_id="test-assign-1",
            accepted=True,
            findings=(),
            missing_evidence=(),
            required_revisions=(),
            integration_eligible=True,
        ))

        # Run the collaboration
        final_state = await orchestrator.run_collaboration(assignments=[assignment])

        # The defect would cause it to reach COMPLETED because of STUB_PASS
        assert final_state.state != CollaborationState.COMPLETED, "Orchestrator allowed a fake success to reach COMPLETED status"
        assert final_state.state == CollaborationState.FAILED, "Orchestrator should fail when verification is missing/invalid"

    asyncio.run(run_test())
