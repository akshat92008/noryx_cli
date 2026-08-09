"""Suite-wide isolation and resource-leak contracts."""

from __future__ import annotations

import os
import threading
import time

import pytest


def _live_model_available() -> bool:
    """Return True if a live model backend is available and not suppressed.

    A backend is considered available when:
    - no offline/disable-network flags are set, AND
    - at least one recognized API key env var is non-empty, OR
    - a local Ollama service is reachable.

    The check is conservative: false-negatives (live backend present but not
    detected) cause live_model tests to be skipped — safe.  False-positives
    (no backend but test runs) would cause spurious CI failures — must avoid.
    """
    # Explicit offline mode always suppresses live tests regardless of keys.
    offline_flags = ("NEXUS_OFFLINE", "NORYX_OFFLINE", "NEXUS_DISABLE_NETWORK")
    if any(os.environ.get(flag, "").strip() not in ("", "0") for flag in offline_flags):
        return False

    api_key_vars = (
        "NVIDIA_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "NEXUS_API_KEY",
        "NORYX_API_KEY",
    )
    if any(os.environ.get(var, "").strip() for var in api_key_vars):
        return True
    # Probe local Ollama without importing requests (may not be installed).
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=0.5)
        return True
    except Exception:
        return False


def pytest_collection_modifyitems(config, items):
    """Skip live_model tests automatically when no model backend is detected."""
    if _live_model_available():
        return  # backend present — run everything
    skip_marker = pytest.mark.skip(
        reason="live_model: no model backend detected (set an API key or start Ollama)"
    )
    for item in items:
        if item.get_closest_marker("live_model"):
            item.add_marker(skip_marker)


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

