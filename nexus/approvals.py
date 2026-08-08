"""Dry-run file mutation previews used by the permission gate."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any


def _resolve(path: str, working_dir: str) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path(working_dir) / candidate
    return candidate.resolve()


def preview_mutation(name: str, args: dict[str, Any], working_dir: str) -> tuple[bool, str]:
    """Return a unified diff without touching the target filesystem."""
    if name == "multi_edit":
        previews = []
        for edit in args.get("edits", []):
            ok, diff = preview_mutation("edit_file", edit, working_dir)
            if not ok:
                return False, diff
            previews.append(diff)
        return True, "\n".join(previews)

    raw_path = args.get("path") or args.get("file_path")
    if not raw_path:
        return False, "No target path was supplied."
    path = _resolve(str(raw_path), working_dir)
    try:
        old = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as exc:
        return False, f"Cannot read {path}: {exc}"

    if name == "write_file":
        new = str(args.get("content", ""))
    elif name == "edit_file":
        needle = str(args.get("old_text", ""))
        if not needle or old.count(needle) != 1:
            return (
                False,
                f"Cannot preview edit: old_text occurs {old.count(needle)} times in {path}.",
            )
        new = old.replace(needle, str(args.get("new_text", "")), 1)
    elif name == "patch_file":
        lines = old.splitlines(keepends=True)
        try:
            start = int(args["start_line"])
            end = int(args["end_line"])
        except (KeyError, TypeError, ValueError):
            return False, "Cannot preview patch: invalid line range."
        replacement = str(args.get("new_content", ""))
        replacement_lines = replacement.splitlines(keepends=True)
        if replacement and not replacement.endswith("\n"):
            replacement_lines[-1] += "\n"
        if end == 0:
            lines[start - 1 : start - 1] = replacement_lines
        else:
            lines[start - 1 : end] = replacement_lines
        new = "".join(lines)
    else:
        return False, f"Unsupported preview tool: {name}"

    diff = "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return True, diff or "(no changes)"
