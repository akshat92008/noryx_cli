"""Flagship Nexus Verified Repair workflow contract."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class VerifiedRepairRequest:
    prompt: str
    budget_inr: float = 20.0
    model: str = "auto"
    routing_mode: str = "balanced"
    working_dir: str = ""
    proof: bool = False
    proof_output: str = ""
    workspace: bool = True
    max_turns: int = 80
    extra_args: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.prompt.strip():
            raise ValueError("repair prompt cannot be empty")
        if self.budget_inr <= 0:
            raise ValueError("budget_inr must be positive")
        if self.max_turns < 1:
            raise ValueError("max_turns must be positive")
        if self.routing_mode not in {"cheapest", "private", "fastest", "balanced", "strongest"}:
            raise ValueError(f"unsupported routing mode: {self.routing_mode}")


@dataclass(frozen=True)
class VerifiedRepairPlan:
    model_key: str
    routing_decision: dict[str, Any] = field(default_factory=dict)
    prompt: str = ""
    cli_args: tuple[str, ...] = ()


_CRITICAL_TERMS = (
    "authentication",
    "authorization",
    "security",
    "payment",
    "billing",
    "encryption",
    "credential",
    "tenant",
    "concurrency",
    "race condition",
    "database migration",
    "schema migration",
    "supply chain",
)
_MULTI_FILE_TERMS = (
    "refactor",
    "multi-file",
    "across the repository",
    "all callers",
    "migration",
    "api and frontend",
    "backend and frontend",
)


def classify_repair(prompt: str) -> tuple[str, int]:
    lowered = prompt.lower()
    risk = "high" if any(term in lowered for term in _CRITICAL_TERMS) else "medium"
    file_count = 4 if any(term in lowered for term in _MULTI_FILE_TERMS) else 1
    return risk, file_count


def select_repair_model(request: VerifiedRepairRequest) -> tuple[str, dict[str, Any]]:
    if request.model != "auto":
        return request.model, {}
    from nexus.model_router import EngineeringPhase, PortfolioMode, model_router

    risk, file_count = classify_repair(request.prompt)
    requirements = model_router.derive_task_requirements(
        "verified-repair",
        phase=EngineeringPhase.DEBUGGING,
        file_count=file_count,
        risk_level=risk,
        context_needed=128_000 if file_count > 1 else 64_000,
    )
    decision = model_router.route(
        requirements,
        mode=PortfolioMode(request.routing_mode.upper()),
        budget_remaining_usd=float(request.budget_inr) / 85.0,
        ask_before_frontier=True,
    )
    return decision.selected_model_key, decision.to_dict()


def build_verified_repair_prompt(task: str) -> str:
    return (
        "[NEXUS VERIFIED REPAIR]\n"
        "Reproduce the defect before editing. Build a decisive repository context map, "
        "including callers, interfaces, tests, data contracts, and security boundaries. "
        "Critique the plan for missed dependencies and scope expansion. Apply the smallest "
        "coherent patch in an isolated workspace. Run targeted checks first, then relevant "
        "regression, security, and integration checks. Never use the model's own completion "
        "claim as evidence. Stop as PARTIALLY_VERIFIED, BLOCKED, or FAILED when the evidence "
        "is incomplete.\n\nTask: "
        + task.strip()
    )


def prepare_verified_repair(request: VerifiedRepairRequest) -> VerifiedRepairPlan:
    request.validate()
    model, decision = select_repair_model(request)
    args = [
        "--print",
        "--mode",
        "quality",
        "--permission-mode",
        "acceptEdits",
        "--routing-mode",
        request.routing_mode,
        "--budget-inr",
        str(float(request.budget_inr)),
        "--max-turns",
        str(request.max_turns),
        "--model",
        model,
        "--local-intern",
        "auto",
        "--workspace" if request.workspace else "--no-workspace",
    ]
    if request.working_dir:
        args.extend(["--working-dir", request.working_dir])
    args.extend(request.extra_args)
    args.append(build_verified_repair_prompt(request.prompt))
    return VerifiedRepairPlan(
        model_key=model,
        routing_decision=decision,
        prompt=args[-1],
        cli_args=tuple(args),
    )
