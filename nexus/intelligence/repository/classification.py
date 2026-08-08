"""File classification module for repository files — Sprint 5."""

from __future__ import annotations

import re
from pathlib import Path
from nexus.intelligence.repository.model import RiskLevel


class FileClassifier:
    """Classifies repository files into category, risk level, and metadata flags."""

    @staticmethod
    def classify(relative_path: str, content_preview: str = "") -> dict:
        path = Path(relative_path)
        name = path.name.lower()
        suffix = path.suffix.lower()
        parts = [part.lower() for part in path.parts]

        # Flags initialization
        is_test = False
        is_config = False
        is_migration = False
        is_generated = False
        is_vendored = False
        is_binary = False
        is_protected = False
        category = "source"
        risk_level = RiskLevel.LOW

        # Secret sensitive / Protected
        if name in {".env", ".env.local", ".env.production", "secrets.yaml", "credentials.json", "id_rsa", "id_ed25519"} or name.endswith(".pem") or name.endswith(".key"):
            category = "secret_sensitive"
            risk_level = RiskLevel.CRITICAL
            is_protected = True

        # Binary extensions
        elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".tar", ".gz", ".pyc", ".so", ".dylib", ".dll", ".exe", ".bin", ".whl"}:
            category = "binary"
            is_binary = True

        # Lockfiles
        elif name in {"uv.lock", "poetry.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "cargo.lock"}:
            category = "lockfile"
            is_config = True

        # Generated or Vendored
        elif any(v in parts for v in {"vendor", "node_modules", "third_party", "generated", "dist", "build"}):
            if "vendor" in parts or "third_party" in parts:
                category = "vendored"
                is_vendored = True
            else:
                category = "generated"
                is_generated = True

        # Test files
        elif (
            "/test/" in f"/{relative_path.lower()}"
            or "/tests/" in f"/{relative_path.lower()}"
            or name.startswith("test_")
            or name.endswith("_test.py")
            or ".test." in name
            or ".spec." in name
            or name.endswith("_test.go")
        ):
            category = "test"
            is_test = True

        # Migration files
        elif "migration" in relative_path.lower() or name.startswith("V") and suffix == ".sql":
            category = "migration"
            is_migration = True
            risk_level = RiskLevel.HIGH

        # Configuration files
        elif (
            name in {
                "package.json", "pyproject.toml", "cargo.toml", "go.mod", "pom.xml",
                "build.gradle", "dockerfile", "makefile", "tsconfig.json", "vite.config.ts",
                "next.config.js", "next.config.mjs", "pytest.ini", "setup.py", "setup.cfg"
            }
            or any(part in {".github", ".nexus", "config", "configs"} for part in parts)
            or suffix in {".yaml", ".yml", ".toml", ".ini"}
        ):
            category = "configuration"
            is_config = True
            if name in {"package.json", "pyproject.toml", "cargo.toml", "go.mod"}:
                risk_level = RiskLevel.MEDIUM

        # Schemas
        elif suffix in {".prisma", ".graphql", ".proto"} or "schema" in name:
            category = "schema"

        # Documentation
        elif suffix in {".md", ".rst", ".txt"} or name in {"readme", "changelog", "license", "contributing"}:
            category = "documentation"

        # Infrastructure / Deployment
        elif "k8s" in parts or "terraform" in parts or "docker" in parts or "deploy" in parts:
            category = "infrastructure"
            risk_level = RiskLevel.MEDIUM

        # High risk areas (Security / Auth / Database / Verification)
        if any(keyword in relative_path.lower() for keyword in {"auth", "security", "crypto", "permission", "verification", "payment", "secret"}):
            if risk_level != RiskLevel.CRITICAL:
                risk_level = RiskLevel.HIGH

        # Content markers for generated code
        if content_preview and ("@generated" in content_preview or "DO NOT EDIT" in content_preview or "auto-generated" in content_preview.lower()):
            is_generated = True
            category = "generated"

        return {
            "category": category,
            "risk_level": risk_level,
            "is_test": is_test,
            "is_config": is_config,
            "is_migration": is_migration,
            "is_generated": is_generated,
            "is_vendored": is_vendored,
            "is_binary": is_binary,
            "is_protected": is_protected,
        }
