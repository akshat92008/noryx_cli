"""Package verification for extension integrity."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from nexus.platform.manifest import ExtensionManifest, ManifestValidationError


@dataclass
class VerificationResult:
    """Result of package verification."""

    valid: bool
    content_hash: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    file_count: int = 0
    total_bytes: int = 0


class PackageVerifier:
    """Verify extension package integrity and safety."""

    ALLOWED_EXTENSIONS = frozenset({".py", ".json", ".md", ".txt", ".yaml", ".yml"})
    FORBIDDEN_PATTERNS = frozenset({
        "__pycache__",
        ".git",
        ".env",
        "credentials",
        "secret",
        ".pem",
        ".key",
    })
    MAX_PACKAGE_BYTES = 50 * 1024 * 1024  # 50 MB
    MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MB per file

    def verify_directory(self, ext_dir: Path) -> VerificationResult:
        """Verify an extension directory."""
        errors: list[str] = []
        warnings: list[str] = []
        hasher = hashlib.sha256()
        file_count = 0
        total_bytes = 0

        if not ext_dir.is_dir():
            return VerificationResult(valid=False, errors=[f"Not a directory: {ext_dir}"])

        manifest_file = ext_dir / "extension.json"
        if not manifest_file.is_file():
            manifest_file = ext_dir / "plugin.json"
        if not manifest_file.is_file():
            return VerificationResult(
                valid=False,
                errors=["Missing extension.json or plugin.json manifest"],
            )

        try:
            manifest = ExtensionManifest.from_file(manifest_file)
        except ManifestValidationError as exc:
            return VerificationResult(valid=False, errors=[str(exc)])

        entry_path = ext_dir / manifest.entry_point
        if not entry_path.is_file():
            errors.append(f"Entry point not found: {manifest.entry_point}")

        for path in sorted(ext_dir.rglob("*")):
            if not path.is_file():
                continue

            rel = path.relative_to(ext_dir)
            rel_str = str(rel)

            for forbidden in self.FORBIDDEN_PATTERNS:
                if forbidden in rel_str.lower():
                    errors.append(f"Forbidden file pattern '{forbidden}' in {rel_str}")
                    continue

            suffix = path.suffix.lower()
            if suffix and suffix not in self.ALLOWED_EXTENSIONS:
                warnings.append(f"Unusual file type: {rel_str}")

            try:
                content = path.read_bytes()
            except OSError as exc:
                errors.append(f"Cannot read {rel_str}: {exc}")
                continue

            size = len(content)
            total_bytes += size
            file_count += 1

            if size > self.MAX_FILE_BYTES:
                errors.append(f"File too large ({size} bytes): {rel_str}")

            hasher.update(rel_str.encode())
            hasher.update(content)

        if total_bytes > self.MAX_PACKAGE_BYTES:
            errors.append(f"Package too large: {total_bytes} bytes (max {self.MAX_PACKAGE_BYTES})")

        return VerificationResult(
            valid=len(errors) == 0,
            content_hash=hasher.hexdigest(),
            errors=errors,
            warnings=warnings,
            file_count=file_count,
            total_bytes=total_bytes,
        )

    def verify_manifest_only(self, manifest_path: Path) -> VerificationResult:
        """Verify only the manifest file."""
        try:
            ExtensionManifest.from_file(manifest_path)
            content = manifest_path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            return VerificationResult(valid=True, content_hash=digest, file_count=1)
        except (ManifestValidationError, OSError) as exc:
            return VerificationResult(valid=False, errors=[str(exc)])

    def compute_content_hash(self, ext_dir: Path) -> str:
        """Compute deterministic content hash for an extension directory."""
        result = self.verify_directory(ext_dir)
        return result.content_hash
