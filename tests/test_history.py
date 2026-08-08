"""
Tests for the history module — file change tracking, undo, and diff.
"""

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nexus.history import FileHistory


def _uid(name: str) -> str:
    """Generate a unique session ID to avoid cross-test pollution."""
    return f"{name}_{int(time.time() * 1_000_000)}"


def test_init_history():
    """Should create a new history session."""
    sid = _uid("test_init")
    h = FileHistory(sid)
    assert h.session_id == sid
    assert h.session_dir.exists()


def test_snapshot_before_write_new_file():
    """Snapshot of a nonexistent file should return None."""
    h = FileHistory(_uid("test_snapshot_new"))
    result = h.snapshot_before_write("/nonexistent/file.txt")
    assert result is None


def test_snapshot_before_write_existing():
    """Snapshot of an existing file should create a backup."""
    h = FileHistory(_uid("test_snapshot_existing"))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("original content")
        tmp_path = f.name

    try:
        snapshot = h.snapshot_before_write(tmp_path)
        assert snapshot is not None
        assert Path(snapshot).exists()
        assert Path(snapshot).read_text() == "original content"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_record_and_get_change():
    """Should record a change and retrieve it."""
    h = FileHistory(_uid("test_record"))
    h.record_change("/tmp/test.txt", "write_file", None, "test change")
    last = h.get_last_change()
    assert last is not None
    assert last["tool"] == "write_file"
    assert last["filepath"].endswith("test.txt")


def test_undo_new_file():
    """Undoing a new file creation should delete the file."""
    h = FileHistory(_uid("test_undo_new"))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("new file content")
        tmp_path = f.name

    h.record_change(tmp_path, "write_file", None, "created new file")
    assert Path(tmp_path).exists()

    success, msg = h.undo_last_change()
    assert success
    assert not Path(tmp_path).exists()


def test_undo_modified_file():
    """Undoing a modification should restore from snapshot."""
    h = FileHistory(_uid("test_undo_mod"))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("version 1")
        tmp_path = f.name

    try:
        snapshot = h.snapshot_before_write(tmp_path)
        # Modify the file
        Path(tmp_path).write_text("version 2")
        h.record_change(tmp_path, "edit_file", snapshot)

        assert Path(tmp_path).read_text() == "version 2"

        success, msg = h.undo_last_change()
        assert success
        assert Path(tmp_path).read_text() == "version 1"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_get_change_summary():
    """Should return a summary of all changes."""
    h = FileHistory(_uid("test_summary"))
    h.record_change("/tmp/a.txt", "write_file", None)
    h.record_change("/tmp/b.txt", "edit_file", "/some/snapshot")

    summary = h.get_change_summary()
    assert "2 file change" in summary
    assert "a.txt" in summary
    assert "b.txt" in summary


def test_get_last_diff_new_file():
    """Should show diff for a newly created file."""
    h = FileHistory(_uid("test_diff_new"))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("new file\nwith content")
        tmp_path = f.name

    h.record_change(tmp_path, "write_file", None)
    diff = h.get_last_diff()
    assert diff is not None
    assert "new file" in diff.lower() or "+new file" in diff or "+with content" in diff

    Path(tmp_path).unlink(missing_ok=True)


def test_empty_history():
    """Empty history should return sensible defaults."""
    h = FileHistory(_uid("test_empty"))
    assert h.get_last_change() is None
    assert h.get_last_diff() is None
    success, msg = h.undo_last_change()
    assert not success
    assert "No changes" in msg
    assert "No file changes" in h.get_change_summary()
