import asyncio
import pytest
from nexus.run_state import RunLedger

@pytest.mark.anyio
async def test_run_ledger_concurrent_events(tmp_path):
    """Bombard the RunLedger with concurrent events to detect thread-safety violations."""
    ledger = RunLedger("test-session", working_dir=tmp_path, root=tmp_path)
    ledger.begin("test request")
    
    async def log_event(i):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: ledger.append_event("test_event", status="completed", detail=f"worker_{i}")
        )
        
    tasks = [log_event(i) for i in range(100)]
    results = await asyncio.gather(*tasks)
    
    assert len(results) == 100
    
    # Verify exactly 100 events were written
    summary = ledger.resume_summary()
    assert summary["state"]["event_count"] == 100
    
    events_path = tmp_path / "runs" / "test-session" / ledger.turn_id / "events.jsonl"
    with open(events_path, "r") as f:
        lines = f.readlines()
        assert len(lines) == 100
        
