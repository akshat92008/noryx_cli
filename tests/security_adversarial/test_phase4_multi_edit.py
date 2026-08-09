"""
tests/security_adversarial/test_phase4_multi_edit.py

Regression tests for the multi_edit TOCTOU race and rollback-safety fixes.

P1 — TOCTOU race
    The hash recorded at read time (originals_hash) is compared against the
    live file hash immediately before each os.replace.  An external write
    that changes the file after read time but before commit must be detected.

    Because the hash check runs BEFORE os.replace is called, monkeypatching
    os.replace cannot reliably inject the race.  Instead we write the
    external change to disk BEFORE the stale-hash check has a chance to run
    by hooking Path.read_bytes on the second call (the commit-phase read).

P1/P2 — Rollback-safety
    The rollback must NOT clobber a file that an external writer modified
    after Noryx's os.replace but before the rollback loop.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from nexus.tools.tools_impl import tool_context, tool_multi_edit


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ─── TOCTOU stale-read detection ─────────────────────────────────────────────

def test_stale_read_lost_update_multi_edit(tmp_path: Path, monkeypatch) -> None:
    """
    External modification between originals_hash recording (phase 1) and
    the stale-hash check (phase 3 commit) must be caught.

    We simulate this by hooking Path.read_bytes so that the SECOND read of
    the target file — which happens in the commit phase to check for staleness
    — returns mutated bytes (as if an external writer changed the file after
    the phase-1 read but before the commit-phase check).
    """
    target = tmp_path / "target.txt"
    target.write_text("line1\nline2\nline3\n")
    target_str = str(target)

    original_read_bytes = Path.read_bytes
    read_count = [0]

    def hooked_read_bytes(self: Path) -> bytes:
        result = original_read_bytes(self)
        if str(self) == target_str:
            read_count[0] += 1
            if read_count[0] == 2:
                # Simulate external write: physically mutate the file now so
                # that our stale-hash check reads the new content.
                self.write_text("external_edit\n")
                return self.read_bytes()  # return what the external writer wrote
        return result

    monkeypatch.setattr(Path, "read_bytes", hooked_read_bytes)

    with tool_context(tmp_path):
        edits = [{"path": str(target), "old_text": "line2", "new_text": "line2_edited"}]
        res = tool_multi_edit(edits)

    assert "❌" in res, f"Expected transaction to abort, got: {res}"
    assert "Stale read detected" in res, f"Wrong error message: {res}"

    final = target.read_text()
    assert "line2_edited" not in final, (
        "multi_edit committed a Noryx edit despite external modification"
    )


def test_multi_edit_succeeds_without_concurrent_writer(tmp_path: Path) -> None:
    """Happy-path: single file, no concurrency → transaction commits."""
    target = tmp_path / "happy.txt"
    target.write_text("aaa\nbbb\nccc\n")

    with tool_context(tmp_path):
        edits = [{"path": str(target), "old_text": "bbb", "new_text": "BBB"}]
        res = tool_multi_edit(edits)

    assert "✅" in res
    assert target.read_text() == "aaa\nBBB\nccc\n"


# ─── Atomicity rollback ───────────────────────────────────────────────────────

def test_multi_edit_atomicity_rollback_no_external_writer(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Edit 1 succeeds (t1 committed), Edit 2 triggers an OSError.
    Because no external writer has modified t1, rollback must restore it
    to its original content.
    """
    t1 = tmp_path / "t1.txt"
    t1.write_text("A\nB\n")
    t2 = tmp_path / "t2.txt"
    t2.write_text("C\nD\n")
    t1_str = str(t1)
    t2_str = str(t2)

    original_replace = os.replace
    calls = []

    def hooked_replace(src, dst):
        calls.append(str(dst))
        # Fail on t2 (second commit)
        if str(dst) == t2_str:
            raise OSError("Injected disk failure on t2 commit")
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", hooked_replace)

    with tool_context(tmp_path):
        edits = [
            {"path": str(t1), "old_text": "A", "new_text": "A2"},
            {"path": str(t2), "old_text": "C", "new_text": "C2"},
        ]
        res = tool_multi_edit(edits)

    assert "❌" in res, f"Expected failure, got: {res}"
    # t1 was committed (os.replace succeeded for it) then rolled back.
    # Since no external writer touched it, rollback must restore the original.
    assert t1.read_text() == "A\nB\n", (
        f"Atomicity failed: t1 not rolled back. Content: {t1.read_text()!r}"
    )


def test_multi_edit_rollback_skips_externally_modified_file(
    tmp_path: Path, monkeypatch
) -> None:
    """
    If an external writer modifies t1 AFTER Noryx commits it (but before
    the rollback loop), the rollback must NOT overwrite the external change.
    """
    t1 = tmp_path / "t1.txt"
    t1.write_text("A\nB\n")
    t2 = tmp_path / "t2.txt"
    t2.write_text("C\nD\n")
    t1_str = str(t1)
    t2_str = str(t2)

    original_replace = os.replace
    calls = []

    def hooked_replace(src, dst):
        calls.append(str(dst))
        if str(dst) == t1_str:
            # Commit t1 normally, then external writer immediately modifies it.
            result = original_replace(src, dst)
            Path(dst).write_text("external_owns_t1\n")
            return result
        if str(dst) == t2_str:
            raise OSError("Injected failure on t2 commit")
        return original_replace(src, dst)

    monkeypatch.setattr(os, "replace", hooked_replace)

    with tool_context(tmp_path):
        edits = [
            {"path": str(t1), "old_text": "A", "new_text": "A2"},
            {"path": str(t2), "old_text": "C", "new_text": "C2"},
        ]
        res = tool_multi_edit(edits)

    assert "❌" in res, f"Expected failure, got: {res}"
    # t1 was externally modified AFTER Noryx committed it — rollback must skip it.
    assert t1.read_text() == "external_owns_t1\n", (
        "Rollback clobbered an external writer's change to t1"
    )


# ─── Multiple edits to the same file ─────────────────────────────────────────

def test_multi_edit_multiple_replacements_same_file(tmp_path: Path) -> None:
    """Sequential edits to the same file in a single transaction must all apply."""
    target = tmp_path / "multi.txt"
    target.write_text("foo\nbar\nbaz\n")

    with tool_context(tmp_path):
        edits = [
            {"path": str(target), "old_text": "foo", "new_text": "FOO"},
            {"path": str(target), "old_text": "bar", "new_text": "BAR"},
        ]
        res = tool_multi_edit(edits)

    assert "✅" in res
    content = target.read_text()
    assert "FOO" in content
    assert "BAR" in content
    assert "foo" not in content
    assert "bar" not in content
