import os

os.environ["NVIDIA_API_KEY"] = "test"
from nexus.agent import Agent
from nexus.pipeline import ExecutionPipeline, PipelineResult


def test_pipeline_preserves_ledger_and_emits_hooks():
    agent = Agent(working_dir=".", workspace_isolation=False)
    pipeline = ExecutionPipeline(agent)

    def mock_analyze(*args, **kwargs):
        return {"plan_type": "direct", "intent": "build", "skills_needed": []}

    def mock_turn(*args, **kwargs):
        return "", []

    agent.planner.analyze = mock_analyze
    agent._run_hosted_turn = mock_turn

    result = pipeline.run("Test simple pipeline without plan", interactive=False)

    assert isinstance(result, PipelineResult)
    assert [item.stage.value for item in result.stage_results] == [
        "repo_understanding",
        "planning",
        "context_selection",
        "model_routing",
        "execution",
        "verification",
        "review",
        "evidence",
        "completion",
    ]
    assert result.status == "UNVERIFIED"
    assert result.outcome == "NO_CHANGES"
    assert result.success is False
    assert agent.run_ledger.resume_summary()["final_report"]["status"] == "UNVERIFIED"


def test_verified_repair_blocks_when_engineering_state_integrity_fails(monkeypatch, tmp_path):
    from nexus.intelligence.engineering import MemoryIntegrityError

    agent = Agent(working_dir=str(tmp_path), workspace_isolation=False)
    pipeline = ExecutionPipeline(agent)
    agent.planner.analyze = lambda *_args, **_kwargs: {
        "plan_type": "direct",
        "intent": "debug",
        "skills_needed": [],
    }

    def corrupt_prepare(*_args, **_kwargs):
        raise MemoryIntegrityError("tampered task memory")

    monkeypatch.setattr(agent.engineering_brain, "prepare", corrupt_prepare)
    result = pipeline.run(
        "[NEXUS VERIFIED REPAIR] Fix service.py and prove the regression is resolved",
        interactive=False,
    )

    assert result.status == "BLOCKED"
    assert result.outcome == "BLOCKED_BY_POLICY"
    assert "safe mutation scope" in result.response
    assert result.stage_results[-1].metadata["hard_block"] is True
