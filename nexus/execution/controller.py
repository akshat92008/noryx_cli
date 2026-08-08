"""
Execution Controller — The central execution authority for Nexus.

Models propose. Runtime executes.
All tool calls, shell commands, file operations, and mutations go through this layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus.process_gateway import ProcessExecutionGateway, ProcessRequest
from nexus.sandbox import CommandResult

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Structured execution result replacing primitive returns."""
    status: str
    command: str = ""
    arguments: list[str] = field(default_factory=list)
    working_directory: str = ""
    environment: dict[str, str] = field(default_factory=dict)
    start_time: str = ""
    end_time: str = ""
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timeout: bool = False
    killed_process: bool = False
    error_type: str = ""
    execution_id: str = ""
    evidence_hash: str = ""
    repository_state_hash: str = ""
    evidence: str = ""

    def __bool__(self) -> bool:
        return self.status == "SUCCESS"


class ExecutionController:
    """Central authority for executing tools and processes safely."""
    
    def __init__(self, workspace_manager: Any = None):
        self.workspace = workspace_manager

    def execute_command(
        self,
        command: list[str] | str,
        cwd: str | Path,
        env: dict[str, str] | None = None,
        timeout: float = 120.0,
        purpose: str = "general_execution",
        network: bool = False,
        isolation_policy: str = "required"
    ) -> ExecutionResult:
        import shlex
        import uuid
        
        exec_id = str(uuid.uuid4())
        start_time = datetime.now(timezone.utc)
        
        if isinstance(command, str):
            cmd_args = shlex.split(command, posix=True)
            cmd_str = command
        else:
            cmd_args = list(command)
            cmd_str = shlex.join(cmd_args)
            
        req = ProcessRequest.create(
            purpose=purpose,
            command=cmd_args,
            workspace=cwd,
            timeout_seconds=timeout,
            env_additions=env,
            network_policy="allow" if network else "deny",
            isolation_policy=isolation_policy,
        )
        
        result: CommandResult = ProcessExecutionGateway.run(req)
        end_time = datetime.now(timezone.utc)
        
        if result.timed_out:
            status = "TIMEOUT"
            error_type = "COMMAND_TIMEOUT"
        elif result.success:
            status = "SUCCESS"
            error_type = ""
        else:
            status = "FAILED"
            error_type = "COMMAND_FAILED"
            
        return ExecutionResult(
            status=status,
            command=cmd_str,
            arguments=result.argv,
            working_directory=result.cwd,
            environment=dict(env or {}),
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
            timeout=result.timed_out,
            killed_process=result.timed_out,
            error_type=error_type,
            execution_id=exec_id,
            evidence=result.format_tool_output()
        )
