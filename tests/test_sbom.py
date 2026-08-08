from __future__ import annotations

import json
from pathlib import Path

from nexus import __version__
from nexus.sbom import build_spdx_sbom, write_spdx_sbom


def test_spdx_sbom_lists_root_and_direct_dependencies(tmp_path: Path):
    payload = build_spdx_sbom(
        ["httpx[socks]>=0.27,<1", "openai>=1.30,<3"],
        created_at="2026-08-06T00:00:00+00:00",
    )
    assert payload["spdxVersion"] == "SPDX-2.3"
    root = next(item for item in payload["packages"] if item["name"] == "nexusai-cli")
    assert root["versionInfo"] == __version__
    names = {item["name"] for item in payload["packages"]}
    assert {"httpx", "openai"} <= names
    assert len(payload["relationships"]) == 2

    output = write_spdx_sbom(
        tmp_path / "sbom.json",
        ["httpx>=0.27,<1"],
        created_at="2026-08-06T00:00:00+00:00",
    )
    assert json.loads(output.read_text(encoding="utf-8"))["documentDescribes"]
