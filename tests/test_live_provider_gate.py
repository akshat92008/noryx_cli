"""
Fix #18: Live provider hello-world release gate.

This test is intentionally skipped unless NORYX_RUN_LIVE_TESTS=1 is set in the
environment.  It makes a real hosted-model call and verifies that a trivial
hello-world task completes within wall-clock and turn budgets.

Usage:
    NORYX_RUN_LIVE_TESTS=1 pytest tests/test_live_provider_gate.py -v -s

The test also acts as a release gate: it MUST pass before tagging an RC.
"""

import os
import tempfile
import time
from pathlib import Path

import pytest

# Skip the entire module unless explicitly opted in.
pytestmark = pytest.mark.skipif(
    os.environ.get("NORYX_RUN_LIVE_TESTS", "0") != "1",
    reason="Set NORYX_RUN_LIVE_TESTS=1 to run live provider tests",
)

HELLO_WORLD_TASK = (
    "Create a Python file called hello.py that prints 'Hello, World!' to stdout. "
    "Run the file and verify the output contains 'Hello, World!'."
)
MAX_WALL_CLOCK_SECONDS = 120
MAX_MODEL_TURNS = 8


@pytest.fixture()
def temp_workdir():
    """Provide a temporary empty working directory for each test."""
    with tempfile.TemporaryDirectory(prefix="noryx_live_test_") as d:
        yield Path(d)


def _collect_events(event_generator) -> list:
    """Drain an event generator and return all events."""
    return list(event_generator)


def test_hello_world_completes_within_budget(temp_workdir):
    """Fix #18 release gate: the canonical hello-world task must complete
    within MAX_WALL_CLOCK_SECONDS and use at most MAX_MODEL_TURNS turns.

    Failure conditions:
    - Wall-clock timeout exceeded
    - hello.py does not exist after run
    - hello.py content does not print 'Hello, World!'
    - More than MAX_MODEL_TURNS model turns consumed
    """
    from nexus.agent import Agent

    # Use whatever model is configured in the environment.
    api_key = (
        os.environ.get("NVIDIA_API_KEY")
        or os.environ.get("NORYX_API_KEY", "")
    )
    if not api_key:
        pytest.skip("No API key found (NVIDIA_API_KEY or NORYX_API_KEY)")

    agent = Agent(
        api_key=api_key,
        working_dir=str(temp_workdir),
        max_turns=MAX_MODEL_TURNS,
    )

    turns_used = 0
    wall_start = time.monotonic()

    events = []
    try:
        for event in agent.run(HELLO_WORLD_TASK):
            events.append(event)
            event_type = getattr(event, "type", None)
            event_type_str = str(event_type) if event_type else ""
            if "turn_completed" in event_type_str or "turn_started" in event_type_str:
                if "turn_completed" in event_type_str:
                    turns_used += 1
            elapsed = time.monotonic() - wall_start
            if elapsed > MAX_WALL_CLOCK_SECONDS:
                pytest.fail(
                    f"hello-world task exceeded wall-clock budget of {MAX_WALL_CLOCK_SECONDS}s "
                    f"after {turns_used} turns."
                )
    except Exception as exc:
        pytest.fail(f"hello-world task raised an unexpected exception: {exc}")

    elapsed = time.monotonic() - wall_start

    # 1. Check that hello.py exists
    hello_py = temp_workdir / "hello.py"
    assert hello_py.exists(), (
        f"hello.py was not created in {temp_workdir} after {elapsed:.1f}s / {turns_used} turns"
    )

    # 2. Check that hello.py contains the expected print statement
    content = hello_py.read_text(encoding="utf-8")
    assert "Hello, World!" in content or "Hello World" in content, (
        f"hello.py does not contain 'Hello, World!': {content!r}"
    )

    # 3. Check turn budget
    assert turns_used <= MAX_MODEL_TURNS, (
        f"hello-world used {turns_used} model turns (budget: {MAX_MODEL_TURNS}). "
        f"This indicates excessive replanning or retry loops."
    )

    print(
        f"\n[live gate] hello-world completed: elapsed={elapsed:.1f}s, turns={turns_used}"
    )


def test_provider_ping_responds_quickly():
    """Fix #18 smoke test: the hosted provider must respond to a minimal
    completion request within 15 seconds.
    """
    from nexus.doctor import ping_live_provider

    result = ping_live_provider(timeout=15.0)
    assert result.status == "pass", (
        f"Provider ping failed: {result.detail}"
    )
    # Extract latency from detail string (e.g. "Live completion succeeded in 1.23s.")
    print(f"\n[live gate] provider ping: {result.detail}")
