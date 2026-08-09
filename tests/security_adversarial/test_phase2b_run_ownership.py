"""
tests/security_adversarial/test_phase2b_run_ownership.py

Regression tests for P2-b (cross-process run ownership) and P3 (structured
degraded-persistence logging) fixes in nexus/run_state.py.

P2-b: RunLedger.begin() writes a PID lockfile (.run.lock) to the turn
directory and refuses to acquire ownership if another live process already
holds the lock.

P3: _set_degraded() and _append_jsonl() now emit WARNING-level structured
log records in addition to the stderr print so that log aggregators can
surface silent storage failures.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest


# ─── P2-b: cross-process run ownership ───────────────────────────────────────

def test_run_lock_created_on_begin(tmp_path: Path) -> None:
    """begin() must create a .run.lock PID file in the turn directory."""
    from nexus.run_state import RunLedger

    ledger = RunLedger(session_id="test-lock-created", working_dir=str(tmp_path), root=tmp_path)
    ledger.begin("first request")

    assert ledger.turn_dir is not None
    lock = ledger.turn_dir / ".run.lock"
    assert lock.exists(), ".run.lock was not created by begin()"
    assert lock.read_text().strip() == str(os.getpid()), (
        ".run.lock does not contain this process's PID"
    )


def test_run_lock_released_on_finalize(tmp_path: Path) -> None:
    """finalize() must remove the .run.lock file."""
    from nexus.run_state import RunLedger, RunStatus

    ledger = RunLedger(session_id="test-lock-release", working_dir=str(tmp_path), root=tmp_path)
    ledger.begin("request")
    turn_dir = ledger.turn_dir
    assert turn_dir is not None

    ledger.finalize(RunStatus.VERIFIED, objective="test")

    lock = turn_dir / ".run.lock"
    assert not lock.exists(), ".run.lock was not removed by finalize()"


def test_run_lock_released_on_mark_rolled_back(tmp_path: Path) -> None:
    """mark_rolled_back() must also remove the .run.lock file."""
    from nexus.run_state import RunLedger

    ledger = RunLedger(session_id="test-lock-rollback", working_dir=str(tmp_path), root=tmp_path)
    ledger.begin("request")
    turn_dir = ledger.turn_dir
    assert turn_dir is not None

    ledger.mark_rolled_back("test rollback")

    lock = turn_dir / ".run.lock"
    assert not lock.exists(), ".run.lock was not removed by mark_rolled_back()"


def test_stale_lock_overwritten_on_dead_process(tmp_path: Path) -> None:
    """
    A .run.lock from a crashed (dead) process must be silently removed and
    the new run must acquire ownership.
    """
    from nexus.run_state import RunLedger

    # Pre-create a session and turn directory to plant a stale lock.
    session_id = "test-stale-lock"
    session_dir = tmp_path / "runs" / session_id
    turn_dir = session_dir / "turn-0001"
    turn_dir.mkdir(parents=True)
    (turn_dir / "checkpoints").mkdir()
    (turn_dir / "patches").mkdir()
    (turn_dir / "tests").mkdir()

    # Write a lock with a PID that is guaranteed to not exist.
    fake_dead_pid = 999999999
    (turn_dir / ".run.lock").write_text(str(fake_dead_pid))

    # begin() on a fresh ledger (which will create turn-0001) should succeed
    # because the lock owner is dead.
    ledger = RunLedger(session_id=session_id, working_dir=str(tmp_path), root=tmp_path)
    # begin() creates the NEXT turn; let's test _acquire_run_lock directly on
    # the pre-planted turn directory.
    ledger._acquire_run_lock(turn_dir)

    lock = turn_dir / ".run.lock"
    assert lock.read_text().strip() == str(os.getpid()), (
        "Stale lock was not replaced with the current PID"
    )


def test_live_lock_raises_runtime_error(tmp_path: Path) -> None:
    """
    A .run.lock owned by a live process must cause _acquire_run_lock to
    raise RuntimeError — two noryx instances must not share a turn.
    """
    from nexus.run_state import RunLedger

    session_dir = tmp_path / "runs" / "test-live-lock"
    turn_dir = session_dir / "turn-0001"
    turn_dir.mkdir(parents=True)

    # Use the CURRENT process PID + 1 to simulate another live process.
    # We override os.kill so it doesn't actually send a signal — we just
    # simulate the existence check returning "alive".
    ledger = RunLedger(
        session_id="test-live-lock", working_dir=str(tmp_path), root=tmp_path
    )

    # Write a lock owned by an arbitrary PID that we'll make appear alive.
    other_pid = os.getpid() + 1
    (turn_dir / ".run.lock").write_text(str(other_pid))

    def fake_kill(pid: int, sig: int) -> None:
        if pid == other_pid and sig == 0:
            return  # process "exists"
        raise ProcessLookupError

    with patch("os.kill", side_effect=fake_kill):
        with pytest.raises(RuntimeError, match="already owned by PID"):
            ledger._acquire_run_lock(turn_dir)


# ─── P3: structured degraded-persistence logging ─────────────────────────────

def test_set_degraded_emits_warning_log(tmp_path: Path, caplog) -> None:
    """_set_degraded() must emit a WARNING-level log record with the structured key."""
    from nexus.run_state import RunLedger

    ledger = RunLedger(session_id="test-degraded-log", working_dir=str(tmp_path), root=tmp_path)
    with caplog.at_level(logging.WARNING, logger="nexus.run_state"):
        ledger._set_degraded()

    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("NORYX_DEGRADED_PERSISTENCE" in m for m in warning_msgs), (
        f"Expected NORYX_DEGRADED_PERSISTENCE in WARNING log, got: {warning_msgs}"
    )


def test_append_jsonl_degraded_emits_warning_log(tmp_path: Path, caplog) -> None:
    """_append_jsonl() in degraded mode must emit a WARNING-level structured log record."""
    from nexus.run_state import RunLedger

    ledger = RunLedger(session_id="test-jsonl-degraded", working_dir=str(tmp_path), root=tmp_path)
    ledger.begin("request")
    # Force degraded mode
    ledger._degraded = True

    with caplog.at_level(logging.WARNING, logger="nexus.run_state"):
        result = ledger._append_jsonl("events.jsonl", {"kind": "test"}, prefix="event")

    assert result == "event-DEGRADED"
    warning_msgs = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("NORYX_DEGRADED_PERSISTENCE" in m for m in warning_msgs), (
        f"Expected degraded-drop WARNING, got: {warning_msgs}"
    )
