"""
Canonical Cost Accounting Ledger, Multi-Currency (INR) Support, Pre-Call Reservation & Cost Intelligence.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from nexus.models import model_registry

DEFAULT_INR_PER_USD = 85.0


@dataclass
class CostEntry:
    entry_id: str
    run_id: str
    phase: str
    provider_id: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    native_currency: str = "USD"
    native_cost: float = 0.0
    display_currency: str = "INR"
    display_cost: float = 0.0
    conversion_rate: float = DEFAULT_INR_PER_USD
    estimated: bool = False
    pricing_version: str = "2026-08"
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "run_id": self.run_id,
            "phase": self.phase,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "usage": {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "cached_tokens": self.cached_tokens,
                "reasoning_tokens": self.reasoning_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens,
            },
            "native_currency": self.native_currency,
            "native_cost": round(self.native_cost, 6),
            "display_currency": self.display_currency,
            "display_cost": round(self.display_cost, 2),
            "conversion_rate": self.conversion_rate,
            "estimated": self.estimated,
            "pricing_version": self.pricing_version,
            "timestamp": self.timestamp,
        }


@dataclass
class CostReservation:
    reservation_id: str
    run_id: str
    model_id: str
    estimated_cost_usd: float
    expires_at: float
    active: bool = True


class CostLedger:
    """Thread-safe canonical cost ledger for Nexus CLI runs."""

    def __init__(self, inr_rate: float = DEFAULT_INR_PER_USD) -> None:
        self._lock = threading.RLock()
        self.inr_rate = inr_rate
        self.entries: list[CostEntry] = []
        self.reservations: dict[str, CostReservation] = {}

    def calculate_cost(
        self,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
    ) -> float:
        """Calculate USD cost for a model call using registered pricing per 1M tokens."""
        desc = model_registry.get_descriptor(model_name)
        if not desc or desc.local:
            return 0.0

        in_rate = (desc.input_cost or 0.0) / 1_000_000
        out_rate = (desc.output_cost or 0.0) / 1_000_000
        cache_rate = (desc.cached_input_cost or in_rate * 0.5) / 1_000_000

        uncached_prompt = max(0, prompt_tokens - cached_tokens)
        cost = (uncached_prompt * in_rate) + (cached_tokens * cache_rate) + (completion_tokens * out_rate)
        return float(cost)

    def record_call(
        self,
        run_id: str,
        phase: str,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
        provider_id: str = "hosted",
        estimated: bool = False,
        reservation_id: str | None = None,
    ) -> CostEntry:
        """Record usage and append a cost entry to the ledger."""
        with self._lock:
            if reservation_id and reservation_id in self.reservations:
                self.reservations[reservation_id].active = False

            desc = model_registry.get_descriptor(model_name)
            eff_model_id = desc.model_id if desc else model_name
            usd_cost = self.calculate_cost(model_name, prompt_tokens, completion_tokens, cached_tokens)
            inr_cost = usd_cost * self.inr_rate

            entry = CostEntry(
                entry_id=f"cost-{hash(time.time() + len(self.entries)) & 0xFFFFFFFF:08x}",
                run_id=run_id,
                phase=phase,
                provider_id=provider_id,
                model_id=eff_model_id,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                reasoning_tokens=reasoning_tokens,
                native_currency="USD",
                native_cost=usd_cost,
                display_currency="INR",
                display_cost=inr_cost,
                conversion_rate=self.inr_rate,
                estimated=estimated,
            )
            self.entries.append(entry)
            return entry

    def reserve_cost(
        self,
        run_id: str,
        model_name: str,
        estimated_prompt_tokens: int = 4000,
        estimated_completion_tokens: int = 2000,
        ttl_seconds: float = 60.0,
    ) -> CostReservation:
        """Atomic pre-call cost reservation."""
        with self._lock:
            est_usd = self.calculate_cost(model_name, estimated_prompt_tokens, estimated_completion_tokens)
            res_id = f"res-{hash(time.monotonic() + len(self.reservations)) & 0xFFFFFFFF:08x}"
            res = CostReservation(
                reservation_id=res_id,
                run_id=run_id,
                model_id=model_name,
                estimated_cost_usd=est_usd,
                expires_at=time.monotonic() + ttl_seconds,
            )
            self.reservations[res_id] = res
            return res

    def get_reserved_cost_usd(self, run_id: str | None = None) -> float:
        """Return total active unexpired reservation amount."""
        with self._lock:
            now = time.monotonic()
            total = 0.0
            for r in self.reservations.values():
                if r.active and r.expires_at > now:
                    if run_id is None or r.run_id == run_id:
                        total += r.estimated_cost_usd
            return total

    def get_total_spend(self, run_id: str | None = None) -> tuple[float, float]:
        """Return (usd_cost, inr_cost) for spent ledger entries."""
        with self._lock:
            usd = 0.0
            inr = 0.0
            for e in self.entries:
                if run_id is None or e.run_id == run_id:
                    usd += e.native_cost
                    inr += e.display_cost
            return usd, inr

    def get_total_projected_spend(self, run_id: str | None = None) -> float:
        """Return total spent USD plus active reservations USD."""
        usd_spent, _ = self.get_total_spend(run_id)
        reserved = self.get_reserved_cost_usd(run_id)
        return usd_spent + reserved

    def snapshot(self, run_id: str | None = None) -> dict[str, Any]:
        """Generate machine-readable summary snapshot."""
        with self._lock:
            usd_spent, inr_spent = self.get_total_spend(run_id)
            reserved_usd = self.get_reserved_cost_usd(run_id)
            by_phase: dict[str, float] = {}
            by_model: dict[str, float] = {}
            prompt_tokens = 0
            completion_tokens = 0

            for e in self.entries:
                if run_id is None or e.run_id == run_id:
                    by_phase[e.phase] = by_phase.get(e.phase, 0.0) + e.native_cost
                    by_model[e.model_id] = by_model.get(e.model_id, 0.0) + e.native_cost
                    prompt_tokens += e.prompt_tokens
                    completion_tokens += e.completion_tokens

            return {
                "run_id": run_id or "all",
                "total_spend_usd": round(usd_spent, 6),
                "total_spend_inr": round(inr_spent, 2),
                "reserved_usd": round(reserved_usd, 6),
                "projected_spend_usd": round(usd_spent + reserved_usd, 6),
                "total_prompt_tokens": prompt_tokens,
                "total_completion_tokens": completion_tokens,
                "conversion_rate": self.inr_rate,
                "spend_by_phase": {k: round(v, 6) for k, v in by_phase.items()},
                "spend_by_model": {k: round(v, 6) for k, v in by_model.items()},
                "entries_count": len(self.entries),
            }

    def save_run_artifacts(self, run_dir: str, run_id: str) -> None:
        """Save ledger artifacts under .nexus/runs/<run-id>/cost/."""
        cost_dir = os.path.join(run_dir, "cost")
        try:
            os.makedirs(cost_dir, exist_ok=True)
            with open(os.path.join(cost_dir, "ledger.json"), "w") as f:
                json.dump([e.to_dict() for e in self.entries if e.run_id == run_id], f, indent=2)
            with open(os.path.join(cost_dir, "summary.json"), "w") as f:
                json.dump(self.snapshot(run_id), f, indent=2)
        except OSError:
            pass


# Global CostLedger singleton
cost_ledger = CostLedger()
