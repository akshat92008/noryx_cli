import os
from pathlib import Path
from nexus.process_gateway import ProcessExecutionGateway, ProcessRequest
from nexus.mutation import MutationController

def test_mutator():
    test_file = Path("test_mutator_target.txt")
    mutator = MutationController(Path("."))
    
    # 1. Write file
    res = mutator.write_file(test_file, "Hello World\n")
    assert res.success, res.error
    assert "Hello World" in res.diff
    
    # 2. Overwrite file
    res2 = mutator.write_file(test_file, "Hello Nexus\n")
    assert res2.success, res2.error
    assert "-Hello World" in res2.diff
    assert "+Hello Nexus" in res2.diff
    
    test_file.unlink()
    print("Mutator tests passed!")

def test_process():
    req = ProcessRequest.create(
        purpose="test_process",
        command=["echo", "Nexus is running"],
        workspace=Path("."),
        isolation_policy="trusted_host",
        network_policy="deny",
    )
    res = ProcessExecutionGateway.run(req)
    assert res.success
    assert "Nexus is running" in res.stdout
    
    # test managed process
    managed = ProcessExecutionGateway.popen(req)
    managed.wait(timeout=5.0)
    assert managed.returncode == 0
    print("Process tests passed!")

if __name__ == "__main__":
    test_mutator()
    test_process()
