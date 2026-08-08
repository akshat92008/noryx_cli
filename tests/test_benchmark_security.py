import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import pytest

from nexus.benchmark import BenchmarkRunner, BenchmarkTask
from tests.support.global_state import reset_global_state

def test_benchmark_does_not_execute_shadowed_module():
    """Verify that benchmark runs in a trusted working directory and prevents module shadowing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        workspace = Path(tmpdir) / "workspace"
        workspace.mkdir()
        
        # Create a malicious nexus.py in the workspace
        malicious_nexus = workspace / "nexus.py"
        malicious_nexus.write_text(
            'import sys; print("MALICIOUS_NEXUS_EXECUTED"); sys.exit(1)'
        )
        
        # Create a task
        task = BenchmarkTask(
            id="test_shadow",
            category="development",
            prompt="do nothing",
            repository=workspace,
            verification=[],
            max_turns_per_attempt=1,
            max_attempts=1,
        )
        
        from nexus.benchmark import BenchmarkSuite
        suite = BenchmarkSuite(name="test_suite", tasks=[task], source=Path(tmpdir))
        runner = BenchmarkRunner(suite)
        
        # Mock the actual ProcessExecutionGateway so we can inspect what command is run
        with mock.patch("nexus.process_gateway.ProcessExecutionGateway.run") as mock_run:
            from nexus.sandbox import CommandResult
            # Mock return value to prevent exceptions
            mock_run.return_value = CommandResult(
                success=True, exit_code=0, stdout="", stderr="", timed_out=False, blocked_reason=None,
                argv=[], cwd="", backend=""
            )
            
            # The benchmark runner runs the task, we want to intercept the gateway
            runner.run(dry_run=False)
            
            # Verify that ProcessExecutionGateway was called with trusted cwd
            assert mock_run.called, "ProcessExecutionGateway was not called"
            call_args = mock_run.call_args[0][0]
            assert call_args.purpose == "benchmark_agent"
            # It should be run from Path.cwd() which is not the workspace
            assert str(call_args.workspace) == str(Path.cwd()), "Process must run from trusted cwd"
            
            # The workspace is passed as an argument, not as cwd
            assert "--working-dir" in call_args.command
            working_dir_idx = call_args.command.index("--working-dir")
            assert call_args.command[working_dir_idx + 1].endswith("workspace")
            
            # Ensure host credentials aren't naively forwarded without filtering
            assert "NEXUS_API_KEY" in call_args.allowed_sensitive_env_keys
