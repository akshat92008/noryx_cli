"""File Mutation Engine.

Wraps file operations to provide atomicity, diffing, and audit trails.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from nexus.events import EventBus, EventType


@dataclass
class MutationResult:
    path: str
    success: bool
    diff: str = ""
    error: str = ""
    hash_before: str = ""
    hash_after: str = ""


class MutationController:
    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).expanduser().resolve()

    def _resolve_and_verify(self, path: str | Path) -> Path:
        target = Path(path).expanduser().resolve()
        if not target.is_absolute():
            target = (self.workspace / path).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError(f"Target path escapes workspace: {path}") from exc
        return target

    def _hash(self, path: Path) -> str:
        if not path.exists():
            return ""
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def write_file(self, path: str | Path, content: str | bytes) -> MutationResult:
        """Write content to a file atomically, returning a unified diff."""
        try:
            target = self._resolve_and_verify(path)
            hash_before = self._hash(target)
            
            lines_before = []
            if target.exists():
                with target.open("r", encoding="utf-8", errors="replace") as f:
                    lines_before = f.readlines()
                    
            target.parent.mkdir(parents=True, exist_ok=True)
            
            # Write to temporary file first for atomicity
            fd, temp_path = tempfile.mkstemp(dir=target.parent, text=isinstance(content, str))
            try:
                with os.fdopen(fd, "w" if isinstance(content, str) else "wb") as f:
                    f.write(content)
                os.replace(temp_path, target)
            except Exception:
                Path(temp_path).unlink(missing_ok=True)
                raise

            hash_after = self._hash(target)
            
            lines_after = []
            with target.open("r", encoding="utf-8", errors="replace") as f:
                lines_after = f.readlines()
                
            diff = "".join(difflib.unified_diff(
                lines_before, lines_after,
                fromfile=f"a/{target.relative_to(self.workspace)}",
                tofile=f"b/{target.relative_to(self.workspace)}"
            ))
            
            result = MutationResult(
                path=str(target),
                success=True,
                diff=diff,
                hash_before=hash_before,
                hash_after=hash_after,
            )
            EventBus.publish(EventType.FILE_MODIFIED, "global", "MutationController", {"path": str(target), "diff": diff})
            return result
            
        except Exception as e:
            return MutationResult(
                path=str(path),
                success=False,
                error=str(e)
            )
