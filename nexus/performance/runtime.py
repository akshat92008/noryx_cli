"""Deterministic performance helpers with bounded memory use."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import tracemalloc
from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from nexus.storage import exclusive_file_lock


@dataclass(frozen=True)
class PerformanceMetric:
    name: str
    duration_ms: float
    memory_peak_kb: float = 0.0
    success: bool = True
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PerformanceBudget:
    name: str
    max_duration_ms: float
    max_memory_kb: float = 0.0
    tolerance_ratio: float = 0.15


@dataclass(frozen=True)
class PerformanceReport:
    generated_at: float
    metrics: tuple[PerformanceMetric, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "nexus.performance.v1",
            "generated_at": self.generated_at,
            "metrics": [asdict(metric) for metric in self.metrics],
        }

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")


class PerformanceProfiler:
    """Small profiler used by release and performance gates."""

    def __init__(self):
        self._metrics: list[PerformanceMetric] = []

    def measure(self, name: str, func: Callable[[], Any]) -> PerformanceMetric:
        tracemalloc.start()
        start = time.perf_counter()
        success = True
        details: dict[str, Any] = {}
        try:
            result = func()
            if isinstance(result, dict):
                details = result
        except Exception as exc:
            success = False
            details = {"error": exc.__class__.__name__, "message": str(exc)}
        duration_ms = (time.perf_counter() - start) * 1000
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        metric = PerformanceMetric(
            name=name,
            duration_ms=round(duration_ms, 3),
            memory_peak_kb=round(peak / 1024, 3),
            success=success,
            details=details,
        )
        self._metrics.append(metric)
        return metric

    def report(self) -> PerformanceReport:
        return PerformanceReport(time.time(), tuple(self._metrics))


class RegressionGate:
    """Compare current metrics to budgets or a stored baseline."""

    def __init__(self, budgets: tuple[PerformanceBudget, ...] = ()):
        self._budgets = {budget.name: budget for budget in budgets}

    def evaluate(self, report: PerformanceReport) -> list[str]:
        failures: list[str] = []
        for metric in report.metrics:
            budget = self._budgets.get(metric.name)
            if not budget:
                continue
            allowed_duration = budget.max_duration_ms * (1 + budget.tolerance_ratio)
            if metric.duration_ms > allowed_duration:
                failures.append(
                    f"{metric.name} duration {metric.duration_ms}ms exceeds {allowed_duration:.3f}ms"
                )
            if budget.max_memory_kb:
                allowed_memory = budget.max_memory_kb * (1 + budget.tolerance_ratio)
                if metric.memory_peak_kb > allowed_memory:
                    failures.append(
                        f"{metric.name} memory {metric.memory_peak_kb}KB exceeds {allowed_memory:.3f}KB"
                    )
        return failures


class ContentHashCache:
    """Content-addressed cache with parser-version invalidation."""

    def __init__(self, state_dir: Path, *, parser_version: str, max_entries: int = 4096):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.parser_version = parser_version
        self.max_entries = max_entries
        self.path = self.state_dir / "content_hash_cache.json"
        self._entries: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if data.get("parser_version") != self.parser_version:
            return
        for key, value in data.get("entries", {}).items():
            self._entries[key] = value

    def _save(self) -> None:
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
        payload = json.dumps(
            {
                "version": "nexus.performance.cache.v2",
                "parser_version": self.parser_version,
                "entries": dict(self._entries),
            },
            indent=2,
            sort_keys=True,
        ) + "\n"
        with exclusive_file_lock(self.path):
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", dir=str(self.path.parent), text=True
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, self.path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)

    def get(self, path: Path) -> dict[str, Any] | None:
        key = str(Path(path).resolve())
        item = self._entries.get(key)
        if not item:
            return None
        if item.get("fingerprint") != _fingerprint(Path(path)):
            self._entries.pop(key, None)
            self._save()
            return None
        self._entries.move_to_end(key)
        return dict(item)

    def put(self, path: Path, value: dict[str, Any]) -> dict[str, Any]:
        file_path = Path(path)
        fingerprint = _fingerprint(file_path)
        item = {
            "fingerprint": fingerprint,
            "content_hash": fingerprint["sha256"],
            "value": value,
            "stored_at": time.time(),
        }
        self._entries[str(file_path.resolve())] = item
        self._entries.move_to_end(str(file_path.resolve()))
        self._save()
        return item


class BoundedEventHistory:
    """Fixed-size event history that returns summaries for evicted events."""

    def __init__(self, max_events: int = 1000):
        self.max_events = max_events
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self.evicted_count = 0

    def append(self, event: dict[str, Any]) -> None:
        if len(self._events) == self.max_events:
            self.evicted_count += 1
        self._events.append({"timestamp": time.time(), **event})

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_events": self.max_events,
            "retained": len(self._events),
            "evicted": self.evicted_count,
            "events": list(self._events),
        }


@dataclass(frozen=True)
class LowResourceProfile:
    max_parallel_workers: int = 1
    max_processes: int = 2
    max_in_memory_graph_nodes: int = 25_000
    max_context_tokens: int = 32_000
    prefer_incremental_indexing: bool = True
    disable_unused_providers: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fingerprint(path: Path) -> dict[str, Any]:
    """Return a content-aware, stable fingerprint.

    Metadata is retained for diagnostics only; cache validity is anchored to bytes.
    The retry loop avoids accepting a file that changed while it was being read.
    """
    file_path = Path(path)
    for _attempt in range(3):
        before = file_path.stat()
        data = file_path.read_bytes()
        after = file_path.stat()
        if (
            before.st_mtime_ns == after.st_mtime_ns
            and before.st_size == after.st_size
            and len(data) == after.st_size
        ):
            return {
                "sha256": hashlib.sha256(data).hexdigest(),
                "mtime_ns": after.st_mtime_ns,
                "size": after.st_size,
            }
    raise OSError(f"File changed repeatedly while fingerprinting: {file_path}")
