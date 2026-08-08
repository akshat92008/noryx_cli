#!/usr/bin/env python3
"""Export every tracked or staged text source file into one deterministic TXT."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path


def _source_paths(root: Path) -> list[Path]:
    completed = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return sorted(
        (root / item.decode("utf-8")).resolve()
        for item in completed.stdout.split(b"\0")
        if item
    )


def export_all_code(root: Path, output: Path) -> tuple[int, str]:
    """Write a complete, deterministic UTF-8 source transcript."""
    root = root.resolve()
    output = output.resolve()
    blocks: list[str] = []
    file_count = 0
    for path in _source_paths(root):
        if path == output or not path.is_file():
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256(raw).hexdigest()
        blocks.append(
            f"===== BEGIN FILE: {relative} =====\n"
            f"SHA256: {digest}\n"
            f"{content.rstrip()}\n"
            f"===== END FILE: {relative} =====\n"
        )
        file_count += 1

    header = (
        "Noryx CLI complete text-source export\n"
        "Format: one UTF-8 block per Git-tracked or unignored text file\n"
        f"Files: {file_count}\n\n"
    )
    payload = header + "\n".join(blocks)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(payload, encoding="utf-8", newline="\n")
    return file_count, hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    count, digest = export_all_code(args.root, args.output)
    print(f"exported {count} text files; sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
