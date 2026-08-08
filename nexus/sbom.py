"""Deterministic SPDX SBOM generation for Nexus release qualification."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from nexus import __version__

_DEPENDENCY_NAME = re.compile(r"^\s*([A-Za-z0-9_.-]+)")


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _dependency_name(requirement: str) -> str:
    match = _DEPENDENCY_NAME.match(requirement)
    if not match:
        raise ValueError(f"Invalid dependency requirement: {requirement!r}")
    return match.group(1)


def _installed_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "NOT-INSTALLED"


def build_spdx_sbom(
    dependencies: Iterable[str],
    *,
    document_name: str = "NexusAI CLI SBOM",
    created_at: str | None = None,
) -> dict[str, Any]:
    """Build a deterministic SPDX 2.3 document for direct runtime dependencies."""
    created = created_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    root_ref = "SPDXRef-Package-nexusai-cli"
    packages: list[dict[str, Any]] = [
        {
            "SPDXID": root_ref,
            "name": "nexusai-cli",
            "versionInfo": __version__,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "MIT",
            "licenseDeclared": "MIT",
            "copyrightText": "NOASSERTION",
            "primaryPackagePurpose": "APPLICATION",
        }
    ]
    relationships: list[dict[str, str]] = []
    seen: set[str] = set()
    for requirement in dependencies:
        name = _dependency_name(str(requirement))
        normalized = _normalized(name)
        if normalized in seen:
            continue
        seen.add(normalized)
        package_ref = "SPDXRef-Package-" + re.sub(r"[^A-Za-z0-9.-]", "-", normalized)
        packages.append(
            {
                "SPDXID": package_ref,
                "name": name,
                "versionInfo": _installed_version(name),
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": f"pkg:pypi/{normalized}",
                    }
                ],
            }
        )
        relationships.append(
            {
                "spdxElementId": root_ref,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package_ref,
            }
        )

    namespace_seed = json.dumps(
        {
            "version": __version__,
            "dependencies": sorted(str(item) for item in dependencies),
        },
        sort_keys=True,
    ).encode("utf-8")
    namespace_hash = hashlib.sha256(namespace_seed).hexdigest()
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": document_name,
        "documentNamespace": f"https://amaura.ai/nexus/sbom/{__version__}/{namespace_hash}",
        "creationInfo": {
            "created": created,
            "creators": ["Tool: NexusAI release qualification"],
        },
        "documentDescribes": [root_ref],
        "packages": packages,
        "relationships": relationships,
    }


def write_spdx_sbom(
    path: str | Path,
    dependencies: Iterable[str],
    *,
    created_at: str | None = None,
) -> Path:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = build_spdx_sbom(dependencies, created_at=created_at)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
