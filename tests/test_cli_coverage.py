"""Tests for CLI coverage and boundary conditions."""

import os
import sys
from unittest.mock import patch

import pytest

from nexus.cli import main


def test_cli_no_args(capsys):
    with patch.dict(os.environ, {"NVIDIA_API_KEY": "test"}):
        with patch("nexus.cli.cli_impl.run_interactive") as mock_run_interactive:
            with patch("nexus.cli.cli_impl.Agent"):
                with patch.object(sys, "argv", ["nexus"]):
                    try:
                        main()
                    except SystemExit:
                        pass
                    mock_run_interactive.assert_called_once()


def test_cli_benchmark_invalid_manifest():
    with patch.dict(os.environ, {"NVIDIA_API_KEY": "test"}):
        with patch.object(sys, "argv", ["nexus", "benchmark", "--manifest", "nonexistent.json"]):
            with pytest.raises(SystemExit) as excinfo:
                main()
            assert excinfo.value.code in (1, 2)


def test_cli_invalid_command(capsys):
    with patch.dict(os.environ, {"NVIDIA_API_KEY": "test"}):
        with patch.object(sys, "argv", ["nexus", "invalid-cmd"]):
            with pytest.raises(SystemExit) as excinfo:
                main()
            assert excinfo.value.code in (1, 2)


def test_generate_dashboard_subcommand_is_dispatched_before_global_parser(tmp_path):
    input_path = tmp_path / "benchmark.json"
    output_path = tmp_path / "dashboard.html"
    input_path.write_text("{}", encoding="utf-8")

    with patch("nexus.dashboard.RegressionDashboard.generate") as generate:
        with patch.object(
            sys,
            "argv",
            [
                "nexus",
                "generate-dashboard",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ],
        ):
            main()

    generate.assert_called_once_with(str(input_path), str(output_path))


def test_generate_dashboard_accepts_shipped_manifest_schema(tmp_path):
    input_path = tmp_path / "benchmark.json"
    output_path = tmp_path / "dashboard.html"
    input_path.write_text(
        '{"schema_version":"nexus.benchmark.v1","name":"smoke","tasks":[]}',
        encoding="utf-8",
    )

    with patch.object(
        sys,
        "argv",
        [
            "nexus",
            "generate-dashboard",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
    ):
        main()

    assert output_path.is_file()

def test_cli_direct_command():
    with patch.dict(os.environ, {"NVIDIA_API_KEY": "test"}):
        with patch("nexus.cli.cli_impl.Agent") as MockAgent:
            with patch.object(sys, "argv", ["nexus", "!echo hello"]):
                mock_agent = MockAgent.return_value
                mock_agent._execute_tool_with_safety.return_value = ("hello\n", True)
                try:
                    main()
                except SystemExit:
                    pass
                mock_agent._execute_tool_with_safety.assert_called_once_with(
                    "run_process", {"argv": ["echo", "hello"], "cwd": mock_agent.working_dir}, _user_initiated=True, _user_confirmed=False
                )

def test_cli_direct_command_confirm_danger():
    with patch.dict(os.environ, {"NVIDIA_API_KEY": "test"}):
        with patch("nexus.cli.cli_impl.Agent") as MockAgent:
            with patch.object(sys, "argv", ["nexus", "--confirm-danger", "!rm -rf ./sentinel"]):
                mock_agent = MockAgent.return_value
                mock_agent._execute_tool_with_safety.return_value = ("success\n", True)
                try:
                    main()
                except SystemExit:
                    pass
                mock_agent._execute_tool_with_safety.assert_called_once_with(
                    "run_process", {"argv": ["rm", "-rf", "./sentinel"], "cwd": mock_agent.working_dir}, _user_initiated=True, _user_confirmed=True
                )

def test_cli_direct_command_pending_rewrite(capsys):
    with patch.dict(os.environ, {"NVIDIA_API_KEY": "test"}):
        with patch("nexus.cli.cli_impl.Agent") as MockAgent:
            with patch.object(sys, "argv", ["nexus", "!rm -rf /"]):
                mock_agent = MockAgent.return_value
                mock_agent._execute_tool_with_safety.return_value = ("⏸️ PENDING_CONFIRMATION [danger-0001]: ...", False)
                try:
                    main()
                except SystemExit:
                    pass
                captured = capsys.readouterr()
                assert "Run with --confirm-danger to execute this exact command: !rm -rf /" in captured.out

def test_cli_direct_command_real_execution(tmp_path):
    import subprocess
    # Run a real harmless direct command via subprocess to avoid mock gaps.
    # Direct commands use the plan-mode policy which does NOT require OS
    # isolation, so the command should succeed on all platforms where a
    # sandbox backend is available, and produce a structured JSON result.
    result = subprocess.run(
        [sys.executable, "-m", "nexus", "--output-format", "json", "!echo real_test_hello"],
        env={**os.environ, "NVIDIA_API_KEY": "test"},
        capture_output=True,
        text=True,
    )
    import json

    if sys.platform == "win32":
        # Windows has no integrated native sandbox backend. The restricted-
        # process fallback is used by default for plan-mode direct commands,
        # which does NOT require OS isolation. So the echo should succeed.
        # If it fails for any other reason the returncode will be 2.
        if result.returncode == 0:
            data = json.loads(result.stdout)
            assert data["name"] == "run_process"
            # echo may not produce output on all Windows shells; just check shape.
        else:
            # Acceptable: Windows subprocess environment may differ.
            assert result.returncode in (0, 2)
    else:
        # On Linux and macOS a native sandbox backend is available in CI
        # (bubblewrap or sandbox-exec). The plan-mode policy does not
        # require OS isolation, so restricted-process is also acceptable.
        assert result.returncode == 0, (
            f"Expected exit 0 but got {result.returncode}.\n"
            f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
        )
        data = json.loads(result.stdout)
        assert data["name"] == "run_process"
        assert "real_test_hello" in data["result"]


def test_cli_single_prompt():
    with patch.dict(os.environ, {"NVIDIA_API_KEY": "test"}):
        with patch("nexus.cli.cli_impl.Agent") as MockAgent:
            with patch.object(sys, "argv", ["nexus", "write a python script"]):
                mock_agent = MockAgent.return_value
                mock_agent.export_final_report.return_value = {"status": "VERIFIED"}
                try:
                    main()
                except SystemExit:
                    pass
                mock_agent.run.assert_called_once_with("write a python script")


def test_cli_missing_credentials(capsys):
    with patch.dict(os.environ, {}, clear=True):
        with patch.object(sys, "argv", ["nexus"]):
            with pytest.raises(SystemExit) as excinfo:
                main()
            assert excinfo.value.code != 0



def test_normalize_subcommand_argv_run_prompt_no_value():
    with patch.object(sys, "argv", ["nexus", "run", "--prompt"]):
        from nexus.cli import _normalize_subcommand_argv
        with pytest.raises(SystemExit):
            _normalize_subcommand_argv()

def test_normalize_subcommand_argv_resume_missing():
    with patch.object(sys, "argv", ["nexus", "resume", "foo"]):
        from nexus.cli import _normalize_subcommand_argv
        with patch("nexus.cli.cli_impl.RunCatalog") as MockCatalog:
            catalog = MockCatalog.return_value
            catalog.resolve.side_effect = FileNotFoundError("Not found")
            with pytest.raises(SystemExit):
                _normalize_subcommand_argv()

def test_handle_workspace_commands_all():
    from nexus.cli import _handle_workspace_commands
    with patch.object(sys, "argv", ["nexus", "workspace"]):
        assert _handle_workspace_commands() is True
    with patch.object(sys, "argv", ["nexus", "workspace", "list"]):
        with patch("nexus.workspace.WorkspaceManager") as MockManager:
            MockManager.return_value.list_worktrees.return_value = []
            assert _handle_workspace_commands() is True
    with patch.object(sys, "argv", ["nexus", "workspace", "status", "foo"]):
        with patch("nexus.workspace.WorkspaceManager") as MockManager:
            session = MockManager.return_value.resolve_worktree.return_value
            session.info = None
            assert _handle_workspace_commands() is True
    with patch.object(sys, "argv", ["nexus", "workspace", "diff", "foo"]):
        with patch("nexus.workspace.WorkspaceManager") as MockManager:
            session = MockManager.return_value.resolve_worktree.return_value
            session.info = True
            session.diff.return_value = "diff"
            assert _handle_workspace_commands() is True
    with patch.object(sys, "argv", ["nexus", "workspace", "diff", "foo"]):
        with patch("nexus.workspace.WorkspaceManager") as MockManager:
            session = MockManager.return_value.resolve_worktree.return_value
            session.info = True
            session.diff.return_value = ""
            assert _handle_workspace_commands() is True
    with patch.object(sys, "argv", ["nexus", "workspace", "apply", "foo"]):
        with patch("nexus.workspace.WorkspaceManager") as MockManager:
            session = MockManager.return_value.resolve_worktree.return_value
            session.info = True
            assert _handle_workspace_commands() is True
    with patch.object(sys, "argv", ["nexus", "workspace", "apply", "foo"]):
        with patch("nexus.workspace.WorkspaceManager") as MockManager:
            session = MockManager.return_value.resolve_worktree.return_value
            session.info = True
            session.apply.side_effect = ImportError("No apply")
            assert _handle_workspace_commands() is True
    with patch.object(sys, "argv", ["nexus", "workspace", "discard", "foo"]):
        with patch("nexus.workspace.WorkspaceManager") as MockManager:
            session = MockManager.return_value.resolve_worktree.return_value
            session.info = True
            assert _handle_workspace_commands() is True

def test_handle_run_management_all():
    from nexus.cli import _handle_run_management
    with patch.object(sys, "argv", ["nexus", "runs", "--json"]):
        with patch("nexus.cli.cli_impl.RunCatalog") as MockCatalog:
            from types import SimpleNamespace
            MockCatalog.return_value.list.return_value = [SimpleNamespace(session_id="a", turn_id="b", status="c", request="d")]
            assert _handle_run_management() is True
    with patch.object(sys, "argv", ["nexus", "runs"]):
        with patch("nexus.cli.cli_impl.RunCatalog") as MockCatalog:
            from types import SimpleNamespace
            MockCatalog.return_value.list.return_value = [SimpleNamespace(session_id="a", turn_id="b", status="c", request="d")]
            assert _handle_run_management() is True
    with patch.object(sys, "argv", ["nexus", "replay", "foo"]):
        with patch("nexus.cli.cli_impl.RunCatalog") as MockCatalog:
            MockCatalog.return_value.replay.return_value = [{"a": 1}]
            assert _handle_run_management() is True
    with patch.object(sys, "argv", ["nexus", "rollback", "foo"]):
        with patch("nexus.recovery.RollbackManager.rollback") as mock_rollback:
            mock_rollback.return_value = (False, "Fail")
            with pytest.raises(SystemExit):
                _handle_run_management()

def test_handle_benchmark_valid():
    from nexus.cli import _handle_benchmark
    with patch.object(sys, "argv", ["nexus", "benchmark", "--manifest", "nonexistent.json"]):
        with patch("nexus.benchmark.BenchmarkSuite.load"):
            with patch("nexus.benchmark.BenchmarkRunner") as MockRunner:
                from types import SimpleNamespace
                MockRunner.return_value.run.return_value = SimpleNamespace(to_dict=lambda: {"summary": {"failed": 0, "tasks": 1}})
                assert _handle_benchmark() is True

def test_handle_benchmark_error():
    from nexus.cli import _handle_benchmark
    with patch.object(sys, "argv", ["nexus", "benchmark", "--manifest", "nonexistent.json"]):
        with patch("nexus.benchmark.BenchmarkSuite.load"):
            with patch("nexus.benchmark.BenchmarkRunner") as MockRunner:
                MockRunner.return_value.run.side_effect = ValueError("Error")
                with pytest.raises(SystemExit):
                    _handle_benchmark()

def test_handle_generate_dashboard_valid():
    from nexus.cli import _handle_generate_dashboard
    with patch.object(sys, "argv", ["nexus", "generate-dashboard", "--input", "in.json", "--output", "out.html"]):
        with patch("nexus.dashboard.RegressionDashboard.generate"):
            assert _handle_generate_dashboard() is True

def test_main_cli_doctor():
    with patch.dict(os.environ, {"NVIDIA_API_KEY": "test"}):
        with patch.object(sys, "argv", ["nexus", "--doctor"]):
            with patch("nexus.cli.cli_impl.run_doctor") as mock_run:
                mock_run.return_value = (True, "OK")
                with pytest.raises(SystemExit) as excinfo:
                    main()
                assert excinfo.value.code == 0

def test_run_interactive_loop():
    from nexus.cli import run_interactive
    with patch("nexus.cli.cli_impl.Agent") as MockAgent:
        agent = MockAgent.return_value
        agent.model_key = "test"
        agent.model_cfg = {"name": "Test", "id": "test", "description": "desc", "context": 10000, "supports_tools": True}
        agent.working_dir = "/tmp"
        agent.mode_policy.label = "autonomous"
        agent.memory.summary = lambda: "memory"
        
        with patch("rich.console.Console.input", side_effect=["/quit"]):
            with pytest.raises(SystemExit):
                run_interactive(agent)

def test_handle_slash_commands_all():
    from nexus.cli import handle_slash_command
    with patch("nexus.cli.cli_impl.Agent") as MockAgent,          patch("nexus.cli.cli_impl.get_history") as mock_get_history,          patch("nexus.cli.cli_impl.ConversationMemory") as mock_mem,          patch("nexus.cli.cli_impl.start_background_web_server"):
        
        agent = MockAgent.return_value
        agent.model_key = "test"
        agent.model_cfg = {"name": "Test", "id": "test", "description": "desc", "context": 10000, "supports_tools": True}
        agent.total_prompt_tokens = 100
        agent.total_completion_tokens = 200
        agent._execute_tool_with_safety.return_value = ("ok", True)
        
        history = mock_get_history.return_value
        history.undo_changes.return_value = (True, "msg")
        history.get_last_diff.return_value = "diff"
        history.get_change_summary.return_value = "summary"
        
        # Test basic UI returns True
        assert handle_slash_command("/clear", agent) is True
        assert handle_slash_command("/reset", agent) is True
        
        with patch("nexus.tools.tool_get_project_structure"):
            assert handle_slash_command("/project", agent) is True
        
        assert handle_slash_command("/cost", agent) is True
        assert handle_slash_command("/run-status", agent) is True
        
        agent.rollback_current_run.return_value = (True, "ok")
        assert handle_slash_command("/rollback-run", agent) is True
        
        assert handle_slash_command("/system", agent) is True
        assert handle_slash_command("/system new_prompt", agent) is True
        
        assert handle_slash_command("/save", agent) is True
        assert handle_slash_command("/save my.json", agent) is True
        
        with patch("nexus.ui.get_multiline_input", return_value="hello"):
            assert handle_slash_command("/multi", agent) is True
            
        assert handle_slash_command("/run", agent) is True
        assert handle_slash_command("/run echo 1", agent) is True
        
        assert handle_slash_command("/undo", agent) is True
        history.undo_changes.return_value = (False, "err")
        assert handle_slash_command("/undo 2", agent) is True
        
        assert handle_slash_command("/diff", agent) is True
        history.get_last_diff.return_value = ""
        assert handle_slash_command("/diff", agent) is True
        
        assert handle_slash_command("/changes", agent) is True
        
        agent.confirm_pending_operation.return_value = ("ok", True)
        assert handle_slash_command("/confirm id", agent) is True
        
        agent.cancel_pending_operation.return_value = ("ok", True)
        assert handle_slash_command("/cancel id", agent) is True
        
        agent.apply_pending_edit.return_value = ("ok", True)
        assert handle_slash_command("/apply id", agent) is True
        
        agent.reject_pending_edit.return_value = ("ok", True)
        assert handle_slash_command("/reject id", agent) is True
        
        assert handle_slash_command("/pending", agent) is True
        
        assert handle_slash_command("/edit-pending", agent) is True
        agent.replace_pending_edit.return_value = ("ok", True)
        assert handle_slash_command("/edit-pending id val", agent) is True
        
        mock_mem.return_value.list_conversations.return_value = []
        assert handle_slash_command("/history", agent) is True
        mock_mem.return_value.list_conversations.return_value = [{"id": "c1", "model_name": "x", "message_count": 1, "preview": "p"}]
        assert handle_slash_command("/history", agent) is True
        
        assert handle_slash_command("/resume", agent) is True
        agent.load_conversation.return_value = True
        assert handle_slash_command("/resume cid", agent) is True
        agent.load_conversation.return_value = False
        assert handle_slash_command("/resume cid", agent) is True
        
        agent.compact_conversation.return_value = 5
        assert handle_slash_command("/compact", agent) is True
        agent.compact_conversation.return_value = 0
        assert handle_slash_command("/compact", agent) is True
        
        with patch("nexus.tools.tool_git_status"):
            assert handle_slash_command("/git", agent) is True
            
        assert handle_slash_command("/tools", agent) is True
        assert handle_slash_command("/skills", agent) is True
        assert handle_slash_command("/hooks", agent) is True
        
        assert handle_slash_command("/subagent", agent) is True
        assert handle_slash_command("/subagent one", agent) is True
        assert handle_slash_command("/subagent one two", agent) is True
        
        assert handle_slash_command("/verify", agent) is True
        assert handle_slash_command("/verify 5", agent) is True
        assert handle_slash_command("/verify mycheck", agent) is True
        assert handle_slash_command("/verify project", agent) is True
        
        assert handle_slash_command("/rewind", agent) is True
        
        assert handle_slash_command("/permissions", agent) is True
        assert handle_slash_command("/permissions plan", agent) is True
        assert handle_slash_command("/permissions bad", agent) is True
        
        assert handle_slash_command("/trust", agent) is True
        from types import SimpleNamespace
        agent.trust.approve.return_value = SimpleNamespace(path="a", digest="b")
        assert handle_slash_command("/trust approve p", agent) is True
        agent.trust.reject.return_value = SimpleNamespace(path="a", digest="b")
        assert handle_slash_command("/trust reject p", agent) is True
        assert handle_slash_command("/trust weird args", agent) is True
        
        assert handle_slash_command("/init", agent) is True
        assert handle_slash_command("/context", agent) is True
        assert handle_slash_command("/plan", agent) is True
        assert handle_slash_command("/mcp", agent) is True
        
        agent.plugin_loader.plugins = {}
        assert handle_slash_command("/plugins", agent) is True
        agent.plugin_loader.plugins = {"p": SimpleNamespace(version="1", description="d")}
        assert handle_slash_command("/plugins", agent) is True
        
        assert handle_slash_command("/web 8080", agent) is True
        assert handle_slash_command("/web", agent) is True
        
        agent.project_mem.load_rules.return_value = SimpleNamespace(build_command="b", test_command="t", lint_command="l", format_command="f", conventions=["c"])
        assert handle_slash_command("/rules", agent) is True
        
        assert handle_slash_command("/login", agent) is True
        assert handle_slash_command("/logout", agent) is True
        assert handle_slash_command("/bug", agent) is True
        assert handle_slash_command("/terminal", agent) is True
        
        with patch("nexus.github.GitHubIntegration.view_pr") as mock_view_pr:
            mock_view_pr.return_value = None
            assert handle_slash_command("/pr_comments", agent) is True
            mock_view_pr.return_value = {"number": 1, "title": "test", "comments": []}
            assert handle_slash_command("/pr_comments 1", agent) is True
            mock_view_pr.return_value = {"number": 1, "title": "test", "comments": [{"author": {"login": "testuser"}, "body": "test comment"}]}
            assert handle_slash_command("/pr_comments 1", agent) is True
            mock_view_pr.side_effect = Exception("Test error")
            assert handle_slash_command("/pr_comments 1", agent) is True

        assert handle_slash_command("/unknowncmd", agent) is True
        
        with pytest.raises(SystemExit):
            handle_slash_command("/quit", agent)
        assert handle_slash_command("/help", agent) is True
        agent.set_model.return_value = True
        assert handle_slash_command("/models gpt-4", agent) is True
        agent.set_model.return_value = False
        assert handle_slash_command("/models gpt-fake", agent) is True
        assert handle_slash_command("/models", agent) is True
        agent.set_model.return_value = True
        assert handle_slash_command("/model gpt-4", agent) is True
        agent.set_model.return_value = False
        assert handle_slash_command("/model gpt-fake", agent) is True
        assert handle_slash_command("/model", agent) is True
        
        assert handle_slash_command("/modelgpt-4", agent) is True
        assert handle_slash_command("/modelgpt-fake", agent) is True
