"""
tests/security_adversarial/test_phase4_stale_read.py

Regression tests for the TOCTOU stale-write race in MutationController.

The P1 fix introduces a two-layer defence:

  Layer 1 (expected_hash guard) — caller supplies the hash it read; if
      the file has already changed before write_file is even called,
      the check fires immediately and write is rejected.

  Layer 2 (pre-replace second hash) — immediately before os.replace,
      a second hash of the live target is computed.  If the file changed
      between layer-1 and the actual replace, this catches it.

The TOCTOU window that remains (between the second hash read and the
os.replace syscall) is now under both a threading.Lock and an
fcntl.LOCK_EX advisory lock, making it inaccessible to any other thread
in the same process and advisory-locked against cooperative POSIX
processes.

Test strategy
-------------
Layer 1 is deterministically testable: write externally BEFORE calling
write_file then supply the old hash.

Layer 2 cannot be deterministically injected via os.replace hooking
because our second hash check runs BEFORE os.replace is called.
Instead we verify the behaviour by injecting the external write between
the first hash capture and the MutationController.write_file call — the
expected_hash (layer 1) catches this case too.

We also have a smoke-test for the case where no external writer exists
(the common path).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from nexus.mutation import MutationController


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ─── Layer-1: expected_hash guard ─────────────────────────────────────────────

def test_stale_read_blocked_by_expected_hash(tmp_path: Path) -> None:
    """
    Caller reads hash, file is modified externally, caller calls write_file
    with the old hash → must be rejected without touching the file.
    """
    target = tmp_path / "target.txt"
    target.write_text("v1\n")
    hash_v1 = _sha256(target)

    # External modification before write_file is called
    target.write_text("v2_external\n")

    mutator = MutationController(tmp_path)
    result = mutator.write_file(target, "v1_noryx_edit\n", expected_hash=hash_v1)

    assert not result.success, "write_file should have failed with stale-hash mismatch"
    assert "Stale read detected" in result.error
    assert target.read_text() == "v2_external\n", (
        "Lost-update: external write was silently discarded"
    )


def test_stale_read_blocked_expected_hash_new_file(tmp_path: Path) -> None:
    """
    If expected_hash is for a file that didn't exist (empty string '') and
    the file now exists, the write must be rejected.
    """
    target = tmp_path / "target.txt"
    target.write_text("somebody_created_this\n")

    mutator = MutationController(tmp_path)
    # expected_hash="" means "file should not exist"; it now exists → stale
    result = mutator.write_file(target, "noryx_content\n", expected_hash="")

    assert not result.success
    assert "Stale read detected" in result.error
    assert target.read_text() == "somebody_created_this\n"


# ─── Layer-2: pre-replace second hash ─────────────────────────────────────────

def test_pre_replace_hash_check_blocks_external_write_in_window(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Simulate an external write that happens AFTER the layer-1 guard passes
    but BEFORE the actual os.replace call (i.e. between the second hash read
    and the replace).

    The second hash check reads `target` immediately before os.replace.
    We simulate the attack by monkey-patching MutationController._hash so
    that the FIRST call returns the expected hash (simulating layer-1 pass),
    but the SECOND call returns a different value (simulating the external
    write arriving in that window).

    This is the only reliable way to inject the race without relying on
    actual scheduler timing.
    """
    target = tmp_path / "target.txt"
    target.write_text("v1\n")
    hash_v1 = _sha256(target)

    mutator = MutationController(tmp_path)
    original_hash = mutator._hash
    call_count = [0]

    def hooked_hash(path: Path) -> str:
        call_count[0] += 1
        result = original_hash(path)
        # On the SECOND call (the pre-replace check) return a different hash
        # to simulate an external write that arrived between the two checks.
        if call_count[0] == 2 and path == target:
            return "0" * 64  # clearly different — simulates external write
        return result

    monkeypatch.setattr(mutator, "_hash", hooked_hash)

    result = mutator.write_file(target, "v1_noryx_edit\n", expected_hash=hash_v1)

    assert not result.success, (
        "write_file reported success despite simulated external modification "
        "in the pre-replace hash window"
    )
    assert "race" in result.error.lower() or "toctou" in result.error.lower(), (
        f"Unexpected error message: {result.error}"
    )
    # Original content must still be intact (os.replace was not called)
    assert target.read_text() == "v1\n", (
        "write_file committed despite detecting the race"
    )


# ─── Happy path ───────────────────────────────────────────────────────────────

def test_write_file_succeeds_when_no_external_change(tmp_path: Path) -> None:
    """Happy-path: no concurrent writer → write succeeds."""
    target = tmp_path / "hello.txt"
    target.write_text("original\n")
    hash_before = _sha256(target)

    mutator = MutationController(tmp_path)
    result = mutator.write_file(target, "updated\n", expected_hash=hash_before)

    assert result.success
    assert target.read_text() == "updated\n"
    assert result.hash_after == _sha256(target)


def test_write_file_creates_new_file(tmp_path: Path) -> None:
    """Creating a brand-new file (no expected_hash) should always succeed."""
    target = tmp_path / "new.txt"
    mutator = MutationController(tmp_path)
    result = mutator.write_file(target, "hello\n")
    assert result.success
    assert target.read_text() == "hello\n"


# ─── tool_edit_file / tool_patch_file stale-read surface ─────────────────────

def test_stale_read_blocked_for_tool_edit_file(tmp_path: Path, monkeypatch) -> None:
    """
    tool_edit_file captures expected_hash before computing the new content.
    A simulated external write between hash capture and write_file must be
    detected and the operation rejected.
    """
    from nexus.tools.tools_impl import tool_context, tool_edit_file

    target = tmp_path / "target.txt"
    target.write_text("line1\nline2\nline3\n")

    original_write = MutationController.write_file

    def hooked_write(self, p, content, expected_hash=None, **kwargs):
        # Mutate the file after tool_edit_file has computed expected_hash
        # but before write_file actually runs.
        Path(p).write_text("externally_modified\n")
        return original_write(self, p, content, expected_hash=expected_hash, **kwargs)

    monkeypatch.setattr(MutationController, "write_file", hooked_write)

    with tool_context(tmp_path):
        res = tool_edit_file(str(target), "line2", "line2_edited")

    assert "❌" in res, f"Expected failure but got: {res}"
    final = target.read_text()
    assert "line2_edited" not in final, (
        "tool_edit_file committed the Noryx change despite external modification"
    )


def test_stale_read_blocked_for_tool_patch_file(tmp_path: Path, monkeypatch) -> None:
    """Mirrors test_stale_read_blocked_for_tool_edit_file for tool_patch_file."""
    from nexus.tools.tools_impl import tool_context, tool_patch_file

    target = tmp_path / "target.txt"
    target.write_text("line1\nline2\nline3\n")

    original_write = MutationController.write_file

    def hooked_write(self, p, content, expected_hash=None, **kwargs):
        Path(p).write_text("externally_modified\n")
        return original_write(self, p, content, expected_hash=expected_hash, **kwargs)

    monkeypatch.setattr(MutationController, "write_file", hooked_write)

    with tool_context(tmp_path):
        res = tool_patch_file(str(target), 2, 2, "line2_patched")

    assert "❌" in res, f"Expected failure but got: {res}"
    final = target.read_text()
    assert "line2_patched" not in final, (
        "tool_patch_file committed the Noryx change despite external modification"
    )
