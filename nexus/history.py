"""
File Change History — tracks every file modification for undo/diff support.

Every write_file, edit_file, patch_file, and multi_edit is recorded here
so the user can review what changed and revert if needed.
"""

import difflib
import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.paths import nexus_home

HISTORY_DIR = nexus_home() / "history"


class FileHistory:
    """Tracks file changes per session for undo and diff operations."""

    def __init__(self, session_id: str | None = None):
        self.session_id = session_id or datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = nexus_home() / "history" / self.session_id
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self.changes: list[dict] = []
        self._load_changes()

    def _changes_file(self) -> Path:
        return self.session_dir / "changes.json"

    def _load_changes(self):
        """Load changes from disk if they exist."""
        cf = self._changes_file()
        if cf.exists():
            try:
                with open(cf, "r") as f:
                    self.changes = json.load(f)
            except (json.JSONDecodeError, OSError):
                self.changes = []

    def _save_changes(self, changes: list[dict] | None = None) -> None:
        """Atomically persist change metadata without exposing a partial JSON file."""
        payload = self.changes if changes is None else changes
        destination = self._changes_file()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(
            f".{destination.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _remove_path(path: Path) -> None:
        """Remove any filesystem object occupying *path* without following links."""
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    def discard_transaction(self, transaction_id: str) -> int:
        """Drop records after the matching transaction was restored independently."""
        if not transaction_id:
            return 0
        retained = [
            item for item in self.changes if item.get("transaction_id") != transaction_id
        ]
        removed = len(self.changes) - len(retained)
        if removed:
            self._save_changes(retained)
            self.changes = retained
        return removed

    def snapshot_before_write(self, filepath: str) -> str | None:
        """
        Capture the current state of a file before it's modified.
        Returns the snapshot path, or None if the file doesn't exist yet.
        """
        p = Path(filepath).expanduser().resolve()
        if not p.exists() or not p.is_file():
            return None

        # Save a copy
        snap_name = f"{len(self.changes):04d}_{p.name}"
        snap_path = self.session_dir / snap_name
        try:
            shutil.copy2(str(p), str(snap_path))
            return str(snap_path)
        except (OSError, shutil.SameFileError):
            return None

    @staticmethod
    def _build_change_entry(
        filepath: str,
        tool_name: str,
        snapshot_path: str | None = None,
        description: str = "",
        *,
        is_new_file: bool | None = None,
        change_type: str = "modified",
        before_sha256: str = "",
        after_sha256: str = "",
        before_mode: int | None = None,
        after_mode: int | None = None,
        before_kind: str = "file",
        before_link_target: str = "",
        transaction_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "timestamp": datetime.now().isoformat(),
            "filepath": str(Path(filepath).expanduser().resolve()),
            "tool": tool_name,
            "snapshot_path": snapshot_path,
            "description": description,
            "is_new_file": snapshot_path is None if is_new_file is None else bool(is_new_file),
            "change_type": change_type,
            "before_sha256": before_sha256,
            "after_sha256": after_sha256,
            "before_mode": before_mode,
            "after_mode": after_mode,
            "before_kind": before_kind,
            "before_link_target": before_link_target,
            "transaction_id": transaction_id,
            "metadata": metadata or {},
        }

    def record_change(
        self,
        filepath: str,
        tool_name: str,
        snapshot_path: str | None = None,
        description: str = "",
        *,
        is_new_file: bool | None = None,
        change_type: str = "modified",
        before_sha256: str = "",
        after_sha256: str = "",
        before_mode: int | None = None,
        after_mode: int | None = None,
        before_kind: str = "file",
        before_link_target: str = "",
        transaction_id: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record one file change using an atomic metadata commit."""
        entry = self._build_change_entry(
            filepath,
            tool_name,
            snapshot_path,
            description,
            is_new_file=is_new_file,
            change_type=change_type,
            before_sha256=before_sha256,
            after_sha256=after_sha256,
            before_mode=before_mode,
            after_mode=after_mode,
            before_kind=before_kind,
            before_link_target=before_link_target,
            transaction_id=transaction_id,
            metadata=metadata,
        )
        candidate = [*self.changes, entry]
        self._save_changes(candidate)
        self.changes = candidate

    def record_changes_batch(self, changes: list[dict[str, Any]]) -> None:
        """Commit a command's complete mutation set in one durable metadata write."""
        if not changes:
            return
        entries = [self._build_change_entry(**change) for change in changes]
        candidate = [*self.changes, *entries]
        self._save_changes(candidate)
        self.changes = candidate

    def get_last_change(self) -> dict | None:
        """Get the most recent file change."""
        return self.changes[-1] if self.changes else None

    def get_recent_changes(self, n: int = 10) -> list[dict]:
        """Get the N most recent changes."""
        return self.changes[-n:]

    def undo_last_change(self) -> tuple[bool, str]:
        """
        Undo the most recent file change.
        Returns (success, message).
        """
        if not self.changes:
            return False, "No changes to undo."

        last = self.changes[-1]
        filepath = last["filepath"]
        snapshot = last.get("snapshot_path")

        target = Path(filepath)
        if last["is_new_file"]:
            # File/symlink was newly created — remove it.
            try:
                self._remove_path(target)
                self.changes.pop()
                self._save_changes()
                return True, f"Deleted newly created path: {filepath}"
            except OSError as e:
                return False, f"Failed to delete {filepath}: {e}"

        before_kind = str(last.get("before_kind") or "file")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            self._remove_path(target)
            if before_kind == "symlink":
                link_target = str(last.get("before_link_target") or "")
                if not link_target:
                    return False, f"Symlink preimage missing for {filepath}"
                target.symlink_to(link_target)
            elif snapshot and Path(snapshot).exists():
                shutil.copyfile(snapshot, target)
                before_mode = last.get("before_mode")
                if before_mode is not None:
                    os.chmod(target, int(before_mode))
                expected = str(last.get("before_sha256") or "")
                if expected:
                    digest = hashlib.sha256(target.read_bytes()).hexdigest()
                    if digest != expected:
                        return False, f"Rollback digest mismatch for {filepath}"
            else:
                return False, f"Snapshot not available for {filepath}; rollback refused."
        except OSError as e:
            return False, f"Failed to restore {filepath}: {e}"

        self.changes.pop()
        self._save_changes()
        return True, (
            f"Restored {target.name} to previous version "
            "(digest-verified pre-command state)"
        )

    def undo_changes(self, count: int = 1) -> tuple[bool, str]:
        """Undo up to *count* operations, newest first, with a complete result list."""
        try:
            count = max(1, int(count))
        except (TypeError, ValueError):
            return False, "Undo count must be a positive integer."
        messages = []
        all_ok = True
        for _ in range(min(count, len(self.changes))):
            ok, message = self.undo_last_change()
            all_ok = all_ok and ok
            messages.append(message)
        if not messages:
            return False, "No changes to undo."
        return all_ok, f"Undid {len(messages)} operation(s):\n" + "\n".join(
            f"  {m}" for m in messages
        )

    def rollback(self, run_id: str | None = None) -> bool:
        """Rollback all recorded changes in this history session."""
        if not self.changes:
            return False
        ok, _ = self.undo_changes(len(self.changes))
        return ok

    def get_recent_diffs(self, count: int = 10) -> str:
        """Render diffs for the most recent operations without changing history."""
        if not self.changes:
            return "No file changes in this session."
        original = self.changes
        rendered = []
        for change in original[-max(1, count) :]:
            self.changes = [change]
            rendered.append(self.get_last_diff() or "(diff unavailable)")
        self.changes = original
        return "\n\n".join(rendered)

    def get_last_diff(self) -> str | None:
        """
        Show a unified diff of the last file change.
        Returns the diff string, or None if not possible.
        """
        if not self.changes:
            return None

        last = self.changes[-1]
        filepath = last["filepath"]
        snapshot = last.get("snapshot_path")

        current_path = Path(filepath)
        if not current_path.exists() and not current_path.is_symlink():
            if snapshot and Path(snapshot).exists():
                try:
                    old_lines = Path(snapshot).read_text(encoding="utf-8", errors="replace").splitlines(True)
                    diff = difflib.unified_diff(
                        old_lines, [], fromfile=f"a/{current_path.name}", tofile="/dev/null", lineterm=""
                    )
                    return "\n".join(diff) or f"Deleted file: {filepath}"
                except OSError:
                    return f"File no longer exists: {filepath}"
            return f"File no longer exists: {filepath}"

        if last["is_new_file"]:
            # Show entire file as "added"
            try:
                with open(current_path, "r", encoding="utf-8", errors="replace") as f:
                    new_lines = f.readlines()
                diff = difflib.unified_diff(
                    [],
                    new_lines,
                    fromfile="/dev/null",
                    tofile=str(current_path.name),
                    lineterm="",
                )
                return "\n".join(diff) or "(empty file created)"
            except OSError:
                return None

        if not snapshot or not Path(snapshot).exists():
            return "Snapshot not available for diff."

        try:
            with open(snapshot, "r", encoding="utf-8", errors="replace") as f:
                old_lines = f.readlines()
            with open(current_path, "r", encoding="utf-8", errors="replace") as f:
                new_lines = f.readlines()

            diff = difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=f"a/{current_path.name}",
                tofile=f"b/{current_path.name}",
                lineterm="",
            )
            result = "\n".join(diff)
            return result if result else "(no differences)"
        except OSError:
            return None

    def get_change_summary(self) -> str:
        """Get a summary of all changes in this session."""
        if not self.changes:
            return "No file changes in this session."

        lines = [f"📋 {len(self.changes)} file change(s) in this session:\n"]
        for i, change in enumerate(self.changes, 1):
            p = Path(change["filepath"])
            action = "created" if change["is_new_file"] else "modified"
            tool = change["tool"]
            ts = change["timestamp"].split("T")[1][:8]
            lines.append(f"  {i}. [{ts}] {action} {p.name} via {tool}")

        return "\n".join(lines)
