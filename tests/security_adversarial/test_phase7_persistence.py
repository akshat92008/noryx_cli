import os
import pytest
from unittest.mock import patch, MagicMock

from nexus.run_state import RunLedger

def test_phase7_degraded_persistence(tmp_path, capsys):
    """Test that Noryx drops into degraded memory mode on ENOSPC without crashing."""
    session_id = "test_degraded_session"
    ledger = RunLedger(session_id, working_dir=str(tmp_path), root=str(tmp_path))
    ledger.begin("test_request")
    
    # Normal append works
    record_id1 = ledger._append_jsonl("events.jsonl", {"tool": "search"}, prefix="evt")
    assert "DEGRADED" not in record_id1
    
    # Simulate ENOSPC in atomic write for checkpoint
    with patch("nexus.run_state.os.replace", side_effect=OSError(28, "No space left on device")):
        path = ledger.checkpoint("test_checkpoint")
        
    # The degraded flag should now be set!
    assert getattr(ledger, "_degraded", False) is True
    
    # The UI warning should have been printed
    captured = capsys.readouterr()
    assert "DEGRADED PERSISTENCE" in captured.out
    
    # Subsequent appends should return DEGRADED and skip disk I/O
    with patch("nexus.run_state.os.open") as mock_open:
        record_id2 = ledger._append_jsonl("events.jsonl", {"tool": "write"}, prefix="evt")
        assert "DEGRADED" in record_id2
        mock_open.assert_not_called()  # Disk I/O was skipped!
        
    # Subsequent checkpoints should skip disk I/O
    with patch("nexus.run_state._atomic_write_json") as mock_atomic:
        path = ledger.checkpoint("test_checkpoint_2")
        mock_atomic.assert_not_called()
