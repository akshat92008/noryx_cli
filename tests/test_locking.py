import os

from nexus.locking import lock_file_descriptor, unlock_file_descriptor
from nexus.mutation import MutationController


def test_locking_helpers_lock_and_unlock_descriptor(tmp_path):
    path = tmp_path / "lock-target.txt"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        assert lock_file_descriptor(fd, exclusive=True, blocking=False) is True
        unlock_file_descriptor(fd)
    finally:
        os.close(fd)


def test_relative_mutation_path_is_resolved_against_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    elsewhere = tmp_path / "elsewhere"
    workspace.mkdir()
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    result = MutationController(workspace).write_file("nested/result.txt", "ok")

    assert result.success is True
    assert (workspace / "nested" / "result.txt").read_text(encoding="utf-8") == "ok"
    assert not (elsewhere / "nested" / "result.txt").exists()
