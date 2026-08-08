from pathlib import Path

from nexus.history import FileHistory
from nexus.recovery import RollbackManager


def test_rollback_manager_no_run(tmp_path: Path):
    success, msg = RollbackManager.rollback("invalid-run-123")
    assert not success
    assert "Could not resolve run" in msg


def test_rollback_manager_successful_rollback(tmp_path: Path, monkeypatch):
    # Mock nexus_home
    monkeypatch.setattr("nexus.run_catalog.nexus_home", lambda: tmp_path)
    monkeypatch.setattr("nexus.paths.nexus_home", lambda: tmp_path)
    monkeypatch.setattr("nexus.run_state.nexus_home", lambda: tmp_path)
    monkeypatch.setattr("nexus.history.nexus_home", lambda: tmp_path)

    # Create a fake session
    session_id = "test-session-001"
    run_id = "turn-0001"

    # Create the run state directory
    turn_dir = tmp_path / "runs" / session_id / run_id
    turn_dir.mkdir(parents=True)

    # Create the request.json to be discoverable
    request_file = turn_dir / "request.json"
    request_file.write_text(
        '{"schema_version": "nexus.run.v1", "session_id": "test-session-001", "turn_id": "turn-0001"}'
    )

    # Create final_report
    final_report = turn_dir / "final_report.json"
    final_report.write_text(
        '{"metadata": {"history_start": 0, "history_end": 1}, "request": {"working_dir": "/tmp"}}'
    )

    # Create FileHistory
    hist = FileHistory(session_id)
    # create a dummy file
    dummy_file = tmp_path / "target.txt"
    dummy_file.write_text("before")

    snap_path = hist.snapshot_before_write(str(dummy_file))

    dummy_file.write_text("after")

    hist.record_change(str(dummy_file), "write_file", snapshot_path=snap_path)

    # Call RollbackManager
    # Note: RollbackManager uses RunCatalog to resolve "turn-0001".
    # RunCatalog looks in all sessions for turn-0001. Since we set nexus_home, it will find it.

    success, msg = RollbackManager.rollback(run_id)

    assert success, msg
    assert "Restored target.txt to previous version" in msg
    assert dummy_file.read_text() == "before"


def test_rollback_manager_blocks_older_turn_with_newer_changes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("nexus.run_catalog.nexus_home", lambda: tmp_path)
    monkeypatch.setattr("nexus.history.nexus_home", lambda: tmp_path)

    session_id = "test-session-newer"
    first_turn = tmp_path / "runs" / session_id / "turn-0001"
    first_turn.mkdir(parents=True)
    (first_turn / "request.json").write_text(
        '{"schema_version":"nexus.run.v2","session_id":"test-session-newer","turn_id":"turn-0001"}'
    )
    (first_turn / "final_report.json").write_text(
        '{"metadata":{"history_start":0,"history_end":1}}'
    )

    history = FileHistory(session_id)
    target = tmp_path / "target-newer.txt"
    target.write_text("v1")
    snap1 = history.snapshot_before_write(str(target))
    target.write_text("v2")
    history.record_change(str(target), "write_file", snapshot_path=snap1)
    snap2 = history.snapshot_before_write(str(target))
    target.write_text("v3")
    history.record_change(str(target), "write_file", snapshot_path=snap2)

    success, message = RollbackManager.rollback("turn-0001")
    assert not success
    assert "newer persisted changes" in message
    assert target.read_text() == "v3"
