"""Extension platform benchmarking."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus.platform.manifest import ExtensionManifest
from nexus.platform.permissions import PermissionScope
from nexus.platform.registry import PlatformExtensionRegistry
from nexus.platform.verification import PackageVerifier


@dataclass
class BenchmarkResult:
    """Result of a single benchmark."""

    name: str
    duration_ms: float
    success: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass
class BenchmarkSuite:
    """Collection of benchmark results."""

    results: list[BenchmarkResult] = field(default_factory=list)
    total_duration_ms: float = 0.0

    def add(self, result: BenchmarkResult) -> None:
        self.results.append(result)
        self.total_duration_ms += result.duration_ms

    def summary(self) -> dict[str, Any]:
        passed = sum(1 for r in self.results if r.success)
        failed = sum(1 for r in self.results if not r.success)
        return {
            "total": len(self.results),
            "passed": passed,
            "failed": failed,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "results": [
                {
                    "name": r.name,
                    "duration_ms": round(r.duration_ms, 2),
                    "success": r.success,
                    "error": r.error,
                }
                for r in self.results
            ],
        }


class ExtensionBenchmark:
    """Benchmark extension platform operations."""

    ITERATIONS = 100

    def __init__(self, *, working_dir: str = ""):
        self.working_dir = working_dir

    def run_all(self, ext_dir: Path | None = None) -> BenchmarkSuite:
        suite = BenchmarkSuite()
        suite.add(self.bench_manifest_validation(ext_dir))
        suite.add(self.bench_permission_check())
        suite.add(self.bench_registry_operations())
        suite.add(self.bench_package_verification(ext_dir))
        return suite

    def bench_manifest_validation(self, ext_dir: Path | None = None) -> BenchmarkResult:
        from nexus.platform.manifest import validate_manifest

        manifest = {
            "name": "bench_ext",
            "version": "1.0.0",
            "extension_type": "tool",
            "capabilities": ["pure"],
        }
        start = time.perf_counter()
        for _ in range(self.ITERATIONS):
            validate_manifest(manifest)
        duration = (time.perf_counter() - start) * 1000
        return BenchmarkResult(
            name="manifest_validation",
            duration_ms=duration,
            success=True,
            details={"iterations": self.ITERATIONS, "avg_ms": duration / self.ITERATIONS},
        )

    def bench_permission_check(self) -> BenchmarkResult:
        from nexus.platform.permissions import PermissionStore

        store = PermissionStore(Path("/tmp/nexus_bench_perms"))
        store.grant("bench", "pure", PermissionScope.GLOBAL)

        start = time.perf_counter()
        for _ in range(self.ITERATIONS):
            store.check("bench", "pure")
        duration = (time.perf_counter() - start) * 1000
        return BenchmarkResult(
            name="permission_check",
            duration_ms=duration,
            success=True,
            details={"iterations": self.ITERATIONS, "avg_ms": duration / self.ITERATIONS},
        )

    def bench_registry_operations(self) -> BenchmarkResult:
        registry = PlatformExtensionRegistry(working_dir="/tmp")

        start = time.perf_counter()
        for i in range(10):
            from nexus.platform.registry import ExtensionRecord
            manifest = ExtensionManifest.from_dict({
                "name": f"bench_{i}",
                "version": "1.0.0",
                "extension_type": "tool",
            })
            record = ExtensionRecord(
                manifest=manifest,
                install_path=f"/tmp/bench_{i}",
            )
            registry.register(record)
        registry.list_extensions()
        duration = (time.perf_counter() - start) * 1000
        return BenchmarkResult(
            name="registry_operations",
            duration_ms=duration,
            success=True,
            details={"registered": 10},
        )

    def bench_package_verification(self, ext_dir: Path | None = None) -> BenchmarkResult:
        if ext_dir is None or not ext_dir.is_dir():
            return BenchmarkResult(
                name="package_verification",
                duration_ms=0,
                success=True,
                details={"skipped": True},
            )

        verifier = PackageVerifier()
        start = time.perf_counter()
        result = verifier.verify_directory(ext_dir)
        duration = (time.perf_counter() - start) * 1000
        return BenchmarkResult(
            name="package_verification",
            duration_ms=duration,
            success=result.valid,
            details={"file_count": result.file_count, "total_bytes": result.total_bytes},
        )
