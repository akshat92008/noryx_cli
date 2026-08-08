"""Tests for Workspace edge cases and cross-platform behaviors."""

from pathlib import Path
from unittest.mock import patch

import pytest

from nexus.workspace import GitWorktreeSession, WorktreeError


def _init_repo(path: Path) -> None:
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "nexus@example.invalid"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Nexus Test"], cwd=path, check=True)
    (path / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=path, check=True)


def test_workspace_snapshots_dirty_git(tmp_path: Path):
    ws_path = tmp_path / "dirty_repo"
    ws_path.mkdir()
    _init_repo(ws_path)
    (ws_path / "tracked.txt").write_text("uncommitted\n", encoding="utf-8")
    (ws_path / "untracked.txt").write_text("new\n", encoding="utf-8")

    session = GitWorktreeSession(ws_path, "session-123", state_root=tmp_path / "state")
    info = session.create()

    isolated = Path(info.path)
    assert info.source_was_dirty is True
    assert info.snapshot_commit
    assert (isolated / "tracked.txt").read_text(encoding="utf-8") == "uncommitted\n"
    assert (isolated / "untracked.txt").read_text(encoding="utf-8") == "new\n"
    assert session.diff() == ""
    session.discard()


def test_workspace_merge_conflict_rejection(tmp_path: Path):
    ws_path = tmp_path / "conflict_repo"
    ws_path.mkdir()
    (ws_path / ".git").mkdir()

    with (
        patch.object(GitWorktreeSession, "_git", return_value=ws_path.as_posix()),
        patch.object(GitWorktreeSession, "_git_bytes", return_value=b"UU conflict.txt\0"),
    ):
        session = GitWorktreeSession(ws_path, "session-456", state_root=tmp_path / "state")
        with pytest.raises(WorktreeError, match="unresolved merge conflicts"):
            session.create()
