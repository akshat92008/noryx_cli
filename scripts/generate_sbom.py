#!/usr/bin/env python3
"""Generate the Nexus direct-dependency SPDX SBOM from pyproject.toml."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nexus.sbom import write_spdx_sbom


def runtime_dependencies(root: Path = ROOT) -> list[str]:
    payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return [str(item) for item in payload.get("project", {}).get("dependencies", [])]


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
