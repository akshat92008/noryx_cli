"""Suite-wide isolation and resource-leak contracts."""

from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture(autouse=True)
def isolate_tool_context_and_detect_resource_leaks():
    """Reset ContextVars and reject non-daemon threads leaked by a test."""
    from tests.support.global_state import reset_global_state
    reset_global_state()
    baseline_threads = {thread.ident for thread in threading.enumerate()}
    yield
    reset_global_state()

    deadline = time.monotonic() + 0.5
    leaked = []
    while time.monotonic() < deadline:
        leaked = [
            thread
            for thread in threading.enumerate()
            if thread.ident not in baseline_threads and thread.is_alive() and not thread.daemon
        ]
        if not leaked:
            break
        for thread in leaked:
            thread.join(timeout=0.02)
    assert not leaked, "non-daemon threads leaked: " + ", ".join(
        f"{thread.name}({thread.ident})" for thread in leaked
    )
