#!/usr/bin/env python3
"""Generate the Noryx runtime SPDX SBOM from the qualified dependency lock."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nexus.sbom import write_spdx_sbom


def runtime_dependencies(root: Path = ROOT) -> list[str]:
    lock = root / "requirements.lock"
    return [
        line.strip()
        for line in lock.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "-"))
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="release_evidence/sbom.spdx.json")
    args = parser.parse_args()
    target = Path(args.output)
    if not target.is_absolute():
        target = ROOT / target
    write_spdx_sbom(target, runtime_dependencies())
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
