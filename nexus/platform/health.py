"""Extension health monitoring."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from nexus.platform.registry import PlatformExtensionRegistry
from nexus.platform.runtime import SecureExtensionRuntime


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
    QUARANTINED = "quarantined"


@dataclass
class HealthReport:
    """Health report for an extension."""

    extension_name: str
    status: HealthStatus
    last_check: float
    uptime_seconds: float = 0.0
    error_count: int = 0
    last_error: str = ""
    worker_running: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class ExtensionHealthMonitor:
    """Monitor extension health and detect failures."""

    MAX_ERROR_COUNT = 5
    CHECK_INTERVAL = 30.0

    def __init__(
        self,
        registry: PlatformExtensionRegistry,
        runtime: SecureExtensionRuntime | None = None,
    ):
        self.registry = registry
        self.runtime = runtime
        self._error_counts: dict[str, int] = {}
        self._start_times: dict[str, float] = {}
        self._last_errors: dict[str, str] = {}

    def check(self, name: str) -> HealthReport:
        """Run a health check on an extension."""
        record = self.registry.get(name)
        if not record:
            return HealthReport(
                extension_name=name,
                status=HealthStatus.UNKNOWN,
                last_check=time.time(),
            )

        if record.health_status == "quarantined":
            return HealthReport(
                extension_name=name,
                status=HealthStatus.QUARANTINED,
                last_check=time.time(),
                last_error=record.error,
            )

        worker_running = False
        if self.runtime:
            worker_running = self.runtime.is_running(name)

        error_count = self._error_counts.get(name, 0)
        status = HealthStatus.HEALTHY

        if error_count >= self.MAX_ERROR_COUNT:
            status = HealthStatus.UNHEALTHY
        elif error_count > 0:
            status = HealthStatus.DEGRADED
        elif record.enabled and not worker_running:
            status = HealthStatus.DEGRADED

        uptime = 0.0
        if name in self._start_times:
            uptime = time.time() - self._start_times[name]

        report = HealthReport(
            extension_name=name,
            status=status,
            last_check=time.time(),
            uptime_seconds=uptime,
            error_count=error_count,
            last_error=self._last_errors.get(name, ""),
            worker_running=worker_running,
        )

        record.last_health_check = report.last_check
        record.health_status = status.value
        return report

    def check_all(self) -> list[HealthReport]:
        return [self.check(r.manifest.name) for r in self.registry.list_extensions()]

    def record_error(self, name: str, error: str) -> None:
        self._error_counts[name] = self._error_counts.get(name, 0) + 1
        self._last_errors[name] = error

        record = self.registry.get(name)
        if record:
            record.error = error
            if self._error_counts[name] >= self.MAX_ERROR_COUNT:
                record.health_status = "unhealthy"

    def record_success(self, name: str) -> None:
        self._error_counts[name] = 0
        self._last_errors.pop(name, None)
        if name not in self._start_times:
            self._start_times[name] = time.time()

    def reset(self, name: str) -> None:
        self._error_counts.pop(name, None)
        self._last_errors.pop(name, None)
        self._start_times.pop(name, None)
