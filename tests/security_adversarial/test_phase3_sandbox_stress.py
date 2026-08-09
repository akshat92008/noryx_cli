import asyncio
import pytest
import time
import os
import signal
from pathlib import Path
from nexus.sandbox import SandboxRunner, CommandSpec

@pytest.mark.anyio
async def test_sandbox_concurrent_subprocesses(tmp_path):
    """Test SandboxRunner under massive concurrent load."""
    runner = SandboxRunner(workspace=tmp_path)
    
    # 50 concurrent echo commands
    async def spawn_echo(i):
        loop = asyncio.get_running_loop()
        spec = CommandSpec.create(
            argv=["echo", f"worker_{i}"],
            cwd=tmp_path,
            allow_unisolated_host_process=True,
            require_os_isolation=False
        )
        return await loop.run_in_executor(None, runner.run, spec)
        
    tasks = [spawn_echo(i) for i in range(50)]
    results = await asyncio.gather(*tasks)
    
    assert len(results) == 50
    for i, res in enumerate(results):
        assert f"worker_{i}" in res.stdout
        assert res.exit_code == 0

@pytest.mark.anyio
async def test_sandbox_timeout_cancellation_race(tmp_path):
    """Test SandboxRunner when a process finishes exactly at timeout boundary, or is cancelled."""
    runner = SandboxRunner(workspace=tmp_path)
    
    # Process sleeps for 1 second, but timeout is exactly 1.0 second.
    # This tries to trigger a race where the subprocess completes exactly as it's killed.
    def run_sleep():
        spec = CommandSpec.create(
            argv=["sleep", "1"],
            cwd=tmp_path,
            timeout_seconds=1.0,
            allow_unisolated_host_process=True,
            require_os_isolation=False
        )
        return runner.run(spec)
        
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, run_sleep)
    
    # The result should either be successful (if sleep finished slightly earlier) 
    # or timed out. It should NOT crash.
    assert result.timed_out or result.exit_code == 0

@pytest.mark.anyio
async def test_sandbox_stdout_saturation(tmp_path):
    """Test SandboxRunner with massive stdout generation (over max_output_bytes)."""
    runner = SandboxRunner(workspace=tmp_path)
    
    def run_dd():
        spec = CommandSpec.create(
            argv=["dd", "if=/dev/zero", "bs=1m", "count=10"], # 10MB of null bytes
            cwd=tmp_path,
            max_output_bytes=100_000, # Max 100KB allowed
            allow_unisolated_host_process=True,
            require_os_isolation=False
        )
        return runner.run(spec)
        
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, run_dd)
    
    # Noryx SandboxRunner must truncate output safely without OOMing or hanging
    assert len(result.stdout.encode("utf-8")) <= 100_000
    assert result.output_truncated
