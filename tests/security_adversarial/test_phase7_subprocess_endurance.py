import os
import psutil
import pytest

from nexus.sandbox import SandboxRunner

def get_process_metrics():
    process = psutil.Process(os.getpid())
    fd_count = process.num_fds()
    children = process.children(recursive=True)
    
    # Check for zombies
    zombies = 0
    for child in children:
        try:
            if child.status() == psutil.STATUS_ZOMBIE:
                zombies += 1
        except psutil.NoSuchProcess:
            pass
            
    return fd_count, len(children), zombies

def test_subprocess_endurance(tmp_path):
    runner = SandboxRunner(str(tmp_path))
    
    baseline_fds, baseline_children, _ = get_process_metrics()
    
    # Spawn 150 subprocesses of varying success/failures
    for i in range(150):
        if i % 3 == 0:
            # Successful short command
            runner.run_shell("echo 'hello'", timeout_seconds=1.0)
        elif i % 3 == 1:
            # Failing command
            runner.run_shell("exit 1", timeout_seconds=1.0)
        else:
            # Timeout command
            runner.run_shell("sleep 5", timeout_seconds=0.1)
            
    # Allow some time for cleanup if any async cleanup happens
    import time
    time.sleep(0.5)
    
    final_fds, final_children, zombies = get_process_metrics()
    
    print(f"\nBaseline FDs: {baseline_fds}, Final FDs: {final_fds}")
    print(f"Baseline Children: {baseline_children}, Final Children: {final_children}")
    print(f"Zombies detected: {zombies}")
    
    # We allow a small FD variance due to pytest internals, but not +150
    assert final_fds - baseline_fds < 10, f"FD leak detected: {final_fds - baseline_fds} FDs leaked"
    assert zombies == 0, "Zombie processes detected!"
    # Orphan processes: we should not have left any lingering children behind
    # other than ones pytest already had
    assert final_children - baseline_children == 0, f"Orphan children detected: {final_children - baseline_children}"
