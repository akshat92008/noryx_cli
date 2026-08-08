"""Capability profiles and deterministic model diagnostics.

Profiles are conservative priors, not marketing claims.  Runtime probe results can
update them, and every profile records its source/confidence so routing decisions
remain auditable.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from nexus.models import ModelTier, model_registry
from nexus.preflight import probe_model


class CapabilityDimension(str, Enum):
    INSTRUCTION_FOLLOWING = "instruction_following"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_ARGUMENTS = "tool_arguments"
    PATH_DISCIPLINE = "path_discipline"
    SINGLE_FILE_REPAIR = "single_file_repair"
    MULTI_FILE_REASONING = "multi_file_reasoning"
    PATCH_VALIDITY = "patch_validity"
    PLAN_QUALITY = "plan_quality"
    DEBUGGING = "debugging"
    RECOVERY_QUALITY = "recovery_quality"
    SECURITY_REASONING = "security_reasoning"


class CapabilityBand(str, Enum):
    UNKNOWN = "unknown"
    UNSUITABLE = "unsuitable"
    LIMITED = "limited"
    SUITABLE = "suitable"
    STRONG = "strong"


@dataclass(frozen=True)
class CapabilityScore:
    score: float
    confidence: float = 0.5
    trials: int = 0
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CapabilityProfile:
    model_id: str
    capabilities: dict[str, CapabilityScore]
    overall_band: CapabilityBand
    source: str = "conservative-prior"
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    probe_ready: bool | None = None
    probe_detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "capabilities": {key: value.to_dict() for key, value in self.capabilities.items()},
            "overall_band": self.overall_band.value,
            "source": self.source,
            "updated_at": self.updated_at,
            "probe_ready": self.probe_ready,
            "probe_detail": self.probe_detail,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CapabilityProfile":
        return cls(
            model_id=str(payload["model_id"]),
            capabilities={
                str(key): CapabilityScore(**value)
                for key, value in dict(payload.get("capabilities", {})).items()
            },
            overall_band=CapabilityBand(payload.get("overall_band", "unknown")),
            source=str(payload.get("source", "loaded")),
            updated_at=str(payload.get("updated_at", datetime.now(timezone.utc).isoformat())),
            probe_ready=payload.get("probe_ready"),
            probe_detail=str(payload.get("probe_detail", "")),
        )


def _band(scores: list[float]) -> CapabilityBand:
    value = sum(scores) / max(1, len(scores))
    if value >= 0.82:
        return CapabilityBand.STRONG
    if value >= 0.62:
        return CapabilityBand.SUITABLE
    if value >= 0.40:
        return CapabilityBand.LIMITED
    return CapabilityBand.UNSUITABLE


def _prior_for(model_name: str) -> CapabilityProfile:
    descriptor = model_registry.get_descriptor(model_name)
    if descriptor is None:
        base = 0.45
        tier = None
        model_id = model_name
    else:
        model_id = descriptor.model_id
        tier = descriptor.tier
        base = {
            ModelTier.LOCAL: 0.46,
            ModelTier.AFFORDABLE: 0.66,
            ModelTier.STRONG: 0.78,
            ModelTier.FRONTIER: 0.86,
        }.get(tier, 0.55)

    dimensions = list(CapabilityDimension)
    values: dict[str, CapabilityScore] = {}
    for dimension in dimensions:
        score = base
        notes: list[str] = ["Conservative tier prior; run model doctor to replace with measured evidence."]
        if tier == ModelTier.LOCAL:
            if dimension in {
                CapabilityDimension.MULTI_FILE_REASONING,
                CapabilityDimension.PLAN_QUALITY,
                CapabilityDimension.RECOVERY_QUALITY,
                CapabilityDimension.SECURITY_REASONING,
            }:
                score -= 0.14
            if dimension in {
                CapabilityDimension.PATH_DISCIPLINE,
                CapabilityDimension.STRUCTURED_OUTPUT,
                CapabilityDimension.PATCH_VALIDITY,
            }:
                score -= 0.05
        elif tier in {ModelTier.STRONG, ModelTier.FRONTIER} and dimension in {
            CapabilityDimension.PLAN_QUALITY,
            CapabilityDimension.MULTI_FILE_REASONING,
            CapabilityDimension.DEBUGGING,
            CapabilityDimension.SECURITY_REASONING,
        }:
            score += 0.04
        score = max(0.05, min(0.95, score))
        values[dimension.value] = CapabilityScore(score=score, confidence=0.35, notes=tuple(notes))
    return CapabilityProfile(model_id=model_id, capabilities=values, overall_band=_band([v.score for v in values.values()]))


class ModelDoctor:
    def __init__(self, store_path: str | Path | None = None) -> None:
        default = Path(os.environ.get("NEXUS_HOME", Path.home() / ".nexusai")) / "model-profiles.json"
        self.store_path = Path(store_path or default).expanduser()
        self._profiles: dict[str, CapabilityProfile] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        for key, value in dict(data).items():
            try:
                self._profiles[key] = CapabilityProfile.from_dict(value)
            except (KeyError, TypeError, ValueError):
                continue

    def _save(self) -> None:
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.store_path.with_suffix(".tmp")
            temp.write_text(
                json.dumps({key: profile.to_dict() for key, profile in self._profiles.items()}, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            temp.replace(self.store_path)
        except OSError:
            # Capability persistence is advisory and must not make routing fail.
            return

    def get_profile(self, model_name: str) -> CapabilityProfile:
        descriptor = model_registry.get_descriptor(model_name)
        canonical = descriptor.model_id if descriptor else model_name
        profile = self._profiles.get(canonical)
        if profile is None:
            profile = _prior_for(model_name)
            self._profiles[canonical] = profile
        return profile

    def register_profile(self, profile: CapabilityProfile) -> None:
        self._profiles[profile.model_id] = profile
        self._save()

    def probe_model(
        self,
        model_name: str,
        *,
        trials_per_probe: int = 1,
        probe_runner: Callable[[str, CapabilityDimension], float | bool] | None = None,
    ) -> CapabilityProfile:
        descriptor = model_registry.get_descriptor(model_name)
        if descriptor is None:
            raise ValueError(f"Unknown model: {model_name}")
        backend_probe = probe_model(descriptor.to_dict(), model_name=model_name)
        prior = _prior_for(model_name)

        measured: dict[str, CapabilityScore] = {}
        for dimension, previous in ((CapabilityDimension(key), value) for key, value in prior.capabilities.items()):
            samples: list[float] = []
            if backend_probe.ready and probe_runner is not None:
                for _ in range(max(1, int(trials_per_probe))):
                    raw = probe_runner(model_name, dimension)
                    samples.append(float(raw) if not isinstance(raw, bool) else (1.0 if raw else 0.0))
            if samples:
                score = max(0.0, min(1.0, sum(samples) / len(samples)))
                measured[dimension.value] = CapabilityScore(
                    score=score,
                    confidence=min(0.95, 0.45 + 0.1 * len(samples)),
                    trials=len(samples),
                    notes=("Measured through injected deterministic probe runner.",),
                )
            else:
                measured[dimension.value] = CapabilityScore(
                    score=previous.score,
                    confidence=previous.confidence,
                    trials=0,
                    notes=previous.notes + (("Backend readiness confirmed; capability benchmark not executed.",) if backend_probe.ready else ("Backend unavailable; retained conservative prior.",)),
                )

        profile = CapabilityProfile(
            model_id=descriptor.model_id,
            capabilities=measured,
            overall_band=_band([item.score for item in measured.values()]),
            source="measured" if probe_runner and backend_probe.ready else "readiness-plus-prior",
            probe_ready=backend_probe.ready,
            probe_detail=backend_probe.detail,
        )
        self.register_profile(profile)
        return profile

    def compare_models(self, first: str, second: str) -> dict[str, Any]:
        a = self.get_profile(first)
        b = self.get_profile(second)
        dimensions: dict[str, Any] = {}
        for dim in CapabilityDimension:
            av = a.capabilities[dim.value].score
            bv = b.capabilities[dim.value].score
            dimensions[dim.value] = {
                "first": av,
                "second": bv,
                "winner": first if av > bv else second if bv > av else "tie",
                "delta": round(av - bv, 4),
            }
        return {
            "first": a.to_dict(),
            "second": b.to_dict(),
            "dimensions": dimensions,
            "warning": "Unmeasured values are conservative priors, not benchmark claims.",
        }


model_doctor = ModelDoctor()
