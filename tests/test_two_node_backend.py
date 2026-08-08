import tempfile
from unittest.mock import MagicMock, patch

from nexus.nova_runtime import AtomicTask, ParsedResponse
from nexus.two_node_backend import SubtaskExecution, TwoNodeBackend


def test_two_node_backend_route_task():
    task_vague = AtomicTask(
        id="1", description="Do something", scope_level="vague", expected_files=1
    )
    route, reason = TwoNodeBackend._route_task(task_vague)
    assert route == "ceiling"

    task_simple = AtomicTask(
        id="5",
        description="Change button color to red in style.css",
        scope_level="precise",
        expected_files=1,
    )
    route, reason = TwoNodeBackend._route_task(task_simple)
    assert route == "nova"


def test_two_node_backend_intern_execution():
    with tempfile.TemporaryDirectory() as temp_dir:
        client_mock = MagicMock()
        backend = TwoNodeBackend(
            client=client_mock,
            ceiling_model_id="ceiling_id",
            ceiling_model_name="ceiling_name",
            working_dir=temp_dir,
        )

        task = AtomicTask(id="1", description="Change red to blue", expected_files=1)

        intern_mock = MagicMock()
        guardrail_mock = MagicMock()
        guardrail_mock.pre_check.return_value = MagicMock(passed=True)
        guardrail_mock.post_check.return_value = MagicMock(passed=True)
        guardrail_mock.schema_check.return_value = MagicMock(passed=True)

        intern_mock.execute.return_value.response = MagicMock(
            raw_text="raw response", is_valid=True, test_command=""
        )

        parser_mock = MagicMock()
        parser_mock.parse.return_value = ParsedResponse(raw_text="raw response")
        backend.parser = parser_mock

        converter_mock = MagicMock()
        converter_mock.propose_tools.return_value = [MagicMock()]

        test_exec_mock = MagicMock()
        test_exec_mock.workspace_dir = temp_dir

        extractor_mock = MagicMock()
        extractor_mock.extract.return_value = []

        verifier_mock = MagicMock()
        verifier_mock.verify.return_value = (True, "passed")

        execution = backend._execute_with_intern(
            task=task,
            validation_prompt="validation",
            intern=intern_mock,
            guardrail=guardrail_mock,
            constraint_extractor=extractor_mock,
            constraint_verifier=verifier_mock,
            test_executor=test_exec_mock,
            context_accumulator="context",
            converter=converter_mock,
        )

        assert execution.verdict == "VALIDATED"
        assert execution.node == "Nova"


def test_two_node_backend_escalate_to_ceiling():
    with tempfile.TemporaryDirectory() as temp_dir:
        client_mock = MagicMock()
        backend = TwoNodeBackend(
            client=client_mock,
            ceiling_model_id="ceiling_id",
            ceiling_model_name="ceiling_name",
            working_dir=temp_dir,
        )

        task = AtomicTask(id="1", description="Change red to blue", expected_files=1)
        previous_execution = SubtaskExecution(task=task, node="Nova", verdict="FAILED", attempts=1)

        with patch.object(backend.ceiling, "execute_direct") as mock_exec_direct:
            mock_exec_direct.return_value = "Ceiling Output"

            parser_mock = MagicMock()
            parser_mock.parse.return_value = ParsedResponse(raw_text="Ceiling Output")
            parser_mock.parse.return_value.files = [
                MagicMock(path="a.txt", content="content", action="create")
            ]
            backend.parser = parser_mock

            converter_mock = MagicMock()
            converter_mock.propose_tools.return_value = [MagicMock()]

            test_exec_mock = MagicMock()
            test_exec_mock.workspace_dir = temp_dir

            execution = backend._escalate_to_ceiling(
                task=task,
                validation_prompt="validation",
                previous=previous_execution,
                test_executor=test_exec_mock,
                converter=converter_mock,
                context_accumulator="context",
            )

            mock_exec_direct.assert_called_once()
            assert "Ceiling Output" in execution.raw_output
            assert execution.verdict == "CEILING_PASS"
            assert execution.node == "Ceiling directly"


def test_two_node_backend_run():
    with tempfile.TemporaryDirectory() as temp_dir:
        client_mock = MagicMock()
        backend = TwoNodeBackend(
            client=client_mock,
            ceiling_model_id="ceiling_id",
            ceiling_model_name="ceiling_name",
            working_dir=temp_dir,
        )

        # Mock decompose
        backend.ceiling.decompose = MagicMock(
            return_value=([AtomicTask(id="1", description="desc", expected_files=1)], "raw decomp")
        )

        # Mock execute methods
        execution = SubtaskExecution(
            task=AtomicTask(id="1", description="desc", expected_files=1),
            node="Nova",
            verdict="VALIDATED",
        )
        execution.verdict = "VALIDATED"
        execution.proposals = [MagicMock()]
        execution.node = "Nova"

        backend._execute_with_intern = MagicMock(return_value=execution)
        backend._route_task = MagicMock(return_value=("nova", "reason"))

        backend.ceiling.review = MagicMock(return_value=(True, "looks good", []))

        res = backend.run(request="Do this", planner_analysis=None)

        assert res.request == "Do this"
        assert res.decomposition_raw == "raw decomp"
        assert res.review_approved is True
        assert len(res.executions) == 1
        assert res.executions[0].verdict == "VALIDATED"
        backend._execute_with_intern.assert_called_once()
        backend.ceiling.decompose.assert_called_once()


def test_two_node_routes_all_multifile_work_to_ceiling():
    task = AtomicTask(
        id=6,
        description="Modify src/a.py and src/b.py to update the helper",
        expected_files=2,
        scope_level="multi_file",
    )
    route, reason = TwoNodeBackend._route_task(task)
    assert route == "ceiling"
    assert "exactly one file" in reason


def test_two_node_intern_requires_explicit_path():
    task = AtomicTask(id=7, description="Fix the off-by-one bug", expected_files=1)
    route, reason = TwoNodeBackend._route_task(task)
    assert route == "ceiling"
    assert "explicit repository path" in reason


def test_two_node_respects_measured_intern_capability_profile():
    task = AtomicTask(id=8, description="Modify src/app.py to rename x to y", expected_files=1)
    weak_profile = {
        "capabilities": {
            "structured_output": {"score": 0.2},
            "path_discipline": {"score": 0.8},
            "single_file_repair": {"score": 0.8},
            "patch_validity": {"score": 0.8},
        }
    }
    route, reason = TwoNodeBackend._route_task(task, weak_profile)
    assert route == "ceiling"
    assert "capability profile" in reason
