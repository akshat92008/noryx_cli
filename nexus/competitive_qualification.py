"""Fail-closed evidence gate for Noryx-versus-Claude Code superiority claims.

A benchmark report is not accepted because it has a high aggregate score.  The
report must describe a sealed, blind, independently evaluated campaign and must
show that Noryx is better on every required hard-task category while remaining
no worse on safety, intervention rate, duration, and cost thresholds.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any, Iterable, Mapping

REQUIRED_HARD_TASK_CATEGORIES = frozenset(
    {
        "hidden_multi_file_bug",
        "framework_migration",
        "feature_addition",
        "difficult_refactor",
        "indirect_test_failure",
        "repository_wide_api_change",
        "state_concurrency_defect",
    }
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PLACEHOLDER_RE = re.compile(r"(?:replace[-_ ]with|placeholder|unsigned[-_ ]until)", re.IGNORECASE)


def _canonical_sha256(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            _contains_placeholder(key) or _contains_placeholder(item) for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_placeholder(item) for item in value)
    return bool(_PLACEHOLDER_RE.search(str(value)))


@dataclass(frozen=True)
class SuperiorityThresholds:
    nexus_agent: str = "nexus"
    direct_baseline_agent: str = "direct_baseline"
    claude_agent: str = "claude_code"
    minimum_unique_tasks: int = 50
    minimum_unique_repositories: int = 10
    minimum_trials_per_task: int = 3
    minimum_tasks_per_category: int = 5
    minimum_nexus_verified_rate: float = 0.75
    minimum_claude_margin: float = 0.05
    minimum_category_nexus_verified_rate: float = 0.60
    minimum_category_claude_margin: float = 0.02
    minimum_direct_uplift: float = 1.25
    minimum_paired_net_wins: int = 1
    maximum_false_success_rate: float = 0.01
    maximum_unexpected_change_rate: float = 0.0
    maximum_cost_ratio_to_claude: float = 0.70
    maximum_duration_ratio_to_claude: float = 1.0
    maximum_intervention_ratio_to_claude: float = 1.0
    required_categories: tuple[str, ...] = tuple(sorted(REQUIRED_HARD_TASK_CATEGORIES))
    require_cost_metrics: bool = True
    require_token_metrics: bool = True
    require_intervention_metrics: bool = True
    require_sealed_provenance: bool = True


@dataclass(frozen=True)
class AgentMetrics:
    runs: int
    available_runs: int
    completed_runs: int
    verified_runs: int
    false_successes: int
    unexpected_change_runs: int
    verified_rate: float
    false_success_rate: float
    unexpected_change_rate: float
    median_duration_ms: float
    median_cost_usd: float | None
    median_input_tokens: float | None
    median_output_tokens: float | None
    median_human_interventions: float | None
    provenance_fingerprints: tuple[str, ...]
    product_identities: tuple[str, ...]
    model_identities: tuple[str, ...]
    executables: tuple[str, ...]
    versions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SuperiorityEvaluation:
    qualified: bool
    status: str
    claim: str
    metrics: dict[str, Any]
    failures: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CampaignTrust:
    """Campaign identity delivered independently from the benchmark report."""

    campaign_id: str
    dataset_revision: str
    manifest_sha256: str
    oracle_bundle_sha256: str
    evaluator_id: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CampaignTrust":
        return cls(
            campaign_id=str(value.get("campaign_id", "")).strip(),
            dataset_revision=str(value.get("dataset_revision", "")).strip(),
            manifest_sha256=str(value.get("manifest_sha256", "")).lower(),
            oracle_bundle_sha256=str(value.get("oracle_bundle_sha256", "")).lower(),
            evaluator_id=str(value.get("evaluator_id", "")).strip(),
        )


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return math.inf if numerator > 0 else None
    return numerator / denominator


def _agent_metrics(records: Iterable[Mapping[str, Any]], agent: str) -> AgentMetrics:
    rows = [dict(item) for item in records if str(item.get("agent")) == agent]
    runs = len(rows)
    costs = [float(item["cost_usd"]) for item in rows if item.get("cost_usd") is not None]
    input_tokens = [
        float(item["input_tokens"]) for item in rows if item.get("input_tokens") is not None
    ]
    output_tokens = [
        float(item["output_tokens"]) for item in rows if item.get("output_tokens") is not None
    ]
    interventions = [
        float(item["human_interventions"])
        for item in rows
        if item.get("human_interventions") is not None
    ]
    durations = [float(item.get("duration_ms") or 0) for item in rows]
    fingerprints: set[str] = set()
    product_identities: set[str] = set()
    model_identities: set[str] = set()
    executables: set[str] = set()
    versions: set[str] = set()
    for item in rows:
        provenance = item.get("provenance") or {}
        if isinstance(provenance, Mapping):
            argv_hash = str(provenance.get("argv_sha256", "")).strip()
            executable = str(provenance.get("executable", "")).strip()
            version = str(provenance.get("version", "")).strip()
            product_identity = str(provenance.get("product_identity", "")).strip()
            model_identity = str(provenance.get("model_identity", "")).strip()
            parts = (argv_hash, executable, version, product_identity, model_identity)
            if any(parts):
                fingerprints.add("|".join(parts))
            if product_identity:
                product_identities.add(product_identity)
            if model_identity:
                model_identities.add(model_identity)
            if executable:
                executables.add(executable)
            if version:
                versions.add(version)
    verified = sum(bool(item.get("verified")) for item in rows)
    false = sum(bool(item.get("false_success")) for item in rows)
    unexpected = sum(bool(item.get("unexpected_files")) for item in rows)
    return AgentMetrics(
        runs=runs,
        available_runs=sum(bool(item.get("available")) for item in rows),
        completed_runs=sum(bool(item.get("completed")) for item in rows),
        verified_runs=verified,
        false_successes=false,
        unexpected_change_runs=unexpected,
        verified_rate=verified / runs if runs else 0.0,
        false_success_rate=false / runs if runs else 1.0,
        unexpected_change_rate=unexpected / runs if runs else 1.0,
        median_duration_ms=median(durations) if durations else 0.0,
        median_cost_usd=median(costs) if costs and len(costs) == runs else None,
        median_input_tokens=(
            median(input_tokens) if input_tokens and len(input_tokens) == runs else None
        ),
        median_output_tokens=(
            median(output_tokens) if output_tokens and len(output_tokens) == runs else None
        ),
        median_human_interventions=(
            median(interventions) if interventions and len(interventions) == runs else None
        ),
        provenance_fingerprints=tuple(sorted(fingerprints)),
        product_identities=tuple(sorted(product_identities)),
        model_identities=tuple(sorted(model_identities)),
        executables=tuple(sorted(executables)),
        versions=tuple(sorted(versions)),
    )


def _require_sealed_campaign(
    report: Mapping[str, Any],
    qualification: Mapping[str, Any],
    failures: list[str],
    *,
    trusted_evaluator_keys: Mapping[str, bytes | str] | None,
    trusted_campaign: CampaignTrust | None,
) -> None:
    required_flags = {
        "blind": True,
        "oracle_withheld": True,
        "private_unseen_tasks": True,
        "independent_evaluator": True,
        "equal_budget_policy": True,
        "task_selection_frozen_before_run": True,
    }
    for key, expected in required_flags.items():
        if qualification.get(key) is not expected:
            failures.append(f"qualification_flag_missing:{key}")

    for key in ("campaign_id", "dataset_revision", "evaluator_id"):
        if not str(qualification.get(key, "")).strip():
            failures.append(f"qualification_identity_missing:{key}")

    for key in (
        "sealed_manifest_sha256",
        "oracle_bundle_sha256",
        "budget_policy_sha256",
        "environment_manifest_sha256",
    ):
        value = str(qualification.get(key, "")).lower()
        if not _SHA256_RE.fullmatch(value):
            failures.append(f"qualification_hash_invalid:{key}")

    budget_policy = qualification.get("budget_policy")
    if not isinstance(budget_policy, Mapping):
        failures.append("qualification_budget_policy_missing")
    else:
        expected = _canonical_sha256(budget_policy)
        if expected != str(qualification.get("budget_policy_sha256", "")).lower():
            failures.append("qualification_budget_policy_hash_mismatch")
        declared_budget = budget_policy.get("declared")
        if not isinstance(declared_budget, Mapping) or not declared_budget:
            failures.append("qualification_declared_budget_missing")
        else:
            required_equalities = (
                "same_repository_revision",
                "same_task_prompt",
                "same_oracle",
                "same_network_policy",
            )
            for key in required_equalities:
                if declared_budget.get(key) is not True:
                    failures.append(f"qualification_budget_not_equal:{key}")
            try:
                wall_time = float(declared_budget.get("maximum_wall_time_seconds_per_run", 0))
            except (TypeError, ValueError):
                wall_time = 0.0
            if wall_time <= 0:
                failures.append("qualification_budget_wall_time_invalid")
            try:
                interventions = int(declared_budget.get("maximum_human_interventions_per_run", -1))
            except (TypeError, ValueError):
                interventions = -1
            if interventions < 0:
                failures.append("qualification_budget_interventions_invalid")

    environment_manifest = qualification.get("environment_manifest")
    if not isinstance(environment_manifest, Mapping):
        failures.append("qualification_environment_manifest_missing")
    else:
        expected = _canonical_sha256(environment_manifest)
        if expected != str(qualification.get("environment_manifest_sha256", "")).lower():
            failures.append("qualification_environment_manifest_hash_mismatch")
        declared_environment = environment_manifest.get("declared")
        runtime_environment = environment_manifest.get("runtime")
        if not isinstance(declared_environment, Mapping) or not declared_environment:
            failures.append("qualification_declared_environment_missing")
        else:
            for key in ("runner_image", "operating_system", "hardware_class"):
                if not str(declared_environment.get(key, "")).strip():
                    failures.append(f"qualification_environment_field_missing:{key}")
            if _contains_placeholder(declared_environment):
                failures.append("qualification_environment_contains_placeholder")
        if not isinstance(runtime_environment, Mapping) or not runtime_environment:
            failures.append("qualification_runtime_environment_missing")
        else:
            for key in ("platform", "machine", "python_version", "harness"):
                if not str(runtime_environment.get(key, "")).strip():
                    failures.append(f"qualification_runtime_field_missing:{key}")

    report_manifest = str(report.get("manifest_sha256", "")).lower()
    sealed_manifest = str(qualification.get("sealed_manifest_sha256", "")).lower()
    if not _SHA256_RE.fullmatch(report_manifest):
        failures.append("report_manifest_sha256_missing")
    elif report_manifest != sealed_manifest:
        failures.append("sealed_manifest_mismatch")

    if trusted_campaign is None:
        failures.append("external_campaign_trust_missing")
    else:
        expected_values = {
            "campaign_id": trusted_campaign.campaign_id,
            "dataset_revision": trusted_campaign.dataset_revision,
            "evaluator_id": trusted_campaign.evaluator_id,
            "sealed_manifest_sha256": trusted_campaign.manifest_sha256,
            "oracle_bundle_sha256": trusted_campaign.oracle_bundle_sha256,
        }
        for key, expected in expected_values.items():
            if not str(expected).strip():
                failures.append(f"external_campaign_trust_invalid:{key}")
                continue
            actual = (
                report_manifest
                if key == "sealed_manifest_sha256"
                else str(qualification.get(key, ""))
            )
            if str(actual).lower() != str(expected).lower():
                failures.append(f"external_campaign_trust_mismatch:{key}")

    from nexus.competitive_attestation import verify_evaluator_signature

    signature_valid, signature_detail = verify_evaluator_signature(
        report, trusted_public_keys=trusted_evaluator_keys
    )
    if not signature_valid:
        failures.append(f"qualification_signature_invalid:{signature_detail}")


def _category_metrics(
    rows: list[Mapping[str, Any]],
    *,
    thresholds: SuperiorityThresholds,
) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    categories = sorted({str(item.get("_category", "unspecified")) for item in rows})
    for category in categories:
        category_rows = [item for item in rows if str(item.get("_category")) == category]
        nexus = _agent_metrics(category_rows, thresholds.nexus_agent)
        baseline = _agent_metrics(category_rows, thresholds.direct_baseline_agent)
        claude = _agent_metrics(category_rows, thresholds.claude_agent)
        task_count = len({str(item.get("_task_id", "")) for item in category_rows})
        output[category] = {
            "tasks": task_count,
            "nexus": nexus.to_dict(),
            "direct_baseline": baseline.to_dict(),
            "claude_code": claude.to_dict(),
            "claude_verified_margin": round(nexus.verified_rate - claude.verified_rate, 6),
            "direct_model_uplift": _safe_ratio(nexus.verified_rate, baseline.verified_rate),
        }
    return output


def _validate_observed_metrics(
    item: Mapping[str, Any],
    *,
    task_id: str,
    trial: int,
    agent: str,
    maximum_wall_time: float,
    maximum_interventions: int,
    failures: list[str],
) -> None:
    try:
        duration_ms = float(item.get("duration_ms", -1))
    except (TypeError, ValueError):
        duration_ms = -1
    if duration_ms < 0:
        failures.append(f"invalid_duration_metric:{task_id}:{trial}:{agent}")
    elif maximum_wall_time > 0 and duration_ms > maximum_wall_time * 1000 + 5000:
        failures.append(f"wall_time_budget_exceeded:{task_id}:{trial}:{agent}")

    for metric_name in (
        "cost_usd",
        "input_tokens",
        "output_tokens",
        "human_interventions",
    ):
        metric_value = item.get(metric_name)
        if metric_value is None:
            continue
        try:
            numeric = float(metric_value)
        except (TypeError, ValueError):
            failures.append(f"invalid_metric:{metric_name}:{task_id}:{trial}:{agent}")
            continue
        if numeric < 0:
            failures.append(f"negative_metric:{metric_name}:{task_id}:{trial}:{agent}")

    interventions_value = item.get("human_interventions")
    if interventions_value is None or maximum_interventions < 0:
        return
    try:
        exceeded = int(interventions_value) > maximum_interventions
    except (TypeError, ValueError):
        return
    if exceeded:
        failures.append(f"intervention_budget_exceeded:{task_id}:{trial}:{agent}")


def _validate_agent_identities(
    agents: Mapping[str, AgentMetrics],
    *,
    thresholds: SuperiorityThresholds,
    failures: list[str],
) -> tuple[AgentMetrics, AgentMetrics, AgentMetrics]:
    for name, metrics in agents.items():
        if len(metrics.product_identities) != 1:
            failures.append(f"agent_product_identity_invalid:{name}")
        elif _contains_placeholder(metrics.product_identities[0]):
            failures.append(f"agent_product_identity_placeholder:{name}")
        if len(metrics.model_identities) != 1:
            failures.append(f"agent_model_identity_invalid:{name}")
        elif _contains_placeholder(metrics.model_identities[0]):
            failures.append(f"agent_model_identity_placeholder:{name}")
        if len(metrics.executables) != 1:
            failures.append(f"agent_executable_identity_invalid:{name}")
        if len(metrics.versions) != 1 or any(
            value.lower() in {"unavailable", "not-declared"} for value in metrics.versions
        ):
            failures.append(f"agent_version_identity_invalid:{name}")

    nexus = agents[thresholds.nexus_agent]
    baseline = agents[thresholds.direct_baseline_agent]
    claude = agents[thresholds.claude_agent]
    nexus_model = nexus.model_identities[0] if len(nexus.model_identities) == 1 else ""
    baseline_model = baseline.model_identities[0] if len(baseline.model_identities) == 1 else ""
    if not nexus_model or nexus_model != baseline_model:
        failures.append("direct_baseline_model_mismatch")
    products = {
        metrics.product_identities[0]
        for metrics in (nexus, baseline, claude)
        if len(metrics.product_identities) == 1
    }
    if len(products) != 3:
        failures.append("agent_product_identities_not_distinct")
    if len(nexus.product_identities) == 1 and "nexus" not in nexus.product_identities[0].lower():
        failures.append("nexus_product_identity_invalid")
    if len(claude.product_identities) == 1 and "claude" not in claude.product_identities[0].lower():
        failures.append("claude_product_identity_invalid")
    return nexus, baseline, claude


def _paired_outcomes(
    group_results: Mapping[tuple[str, int], Mapping[str, Mapping[str, Any]]],
    *,
    nexus_agent: str,
    claude_agent: str,
) -> tuple[int, int, int]:
    wins = losses = ties = 0
    for per_agent in group_results.values():
        nexus_result = per_agent.get(nexus_agent)
        claude_result = per_agent.get(claude_agent)
        if nexus_result is None or claude_result is None:
            continue
        nexus_verified = bool(nexus_result.get("verified"))
        claude_verified = bool(claude_result.get("verified"))
        if nexus_verified and not claude_verified:
            wins += 1
        elif claude_verified and not nexus_verified:
            losses += 1
        else:
            ties += 1
    return wins, losses, ties


def _validate_category_thresholds(
    category_metrics: Mapping[str, Mapping[str, Any]],
    *,
    thresholds: SuperiorityThresholds,
    failures: list[str],
) -> None:
    for category in thresholds.required_categories:
        category_result = category_metrics.get(category)
        if not category_result:
            continue
        nexus_category = category_result["nexus"]
        margin = float(category_result["claude_verified_margin"] or 0)
        if float(nexus_category["verified_rate"]) < thresholds.minimum_category_nexus_verified_rate:
            failures.append(f"category_verified_rate_below_threshold:{category}")
        if margin < thresholds.minimum_category_claude_margin:
            failures.append(f"category_claude_margin_below_threshold:{category}")
        if float(nexus_category["false_success_rate"]) > float(
            category_result["claude_code"]["false_success_rate"]
        ):
            failures.append(f"category_false_success_worse:{category}")
        if float(nexus_category["unexpected_change_rate"]) > float(
            category_result["claude_code"]["unexpected_change_rate"]
        ):
            failures.append(f"category_unexpected_changes_worse:{category}")


def evaluate_superiority_report(
    report: Mapping[str, Any],
    *,
    thresholds: SuperiorityThresholds | None = None,
    trusted_evaluator_keys: Mapping[str, bytes | str] | None = None,
    trusted_campaign: CampaignTrust | Mapping[str, Any] | None = None,
) -> SuperiorityEvaluation:
    thresholds = thresholds or SuperiorityThresholds()
    failures: list[str] = []
    if bool(report.get("dry_run")):
        failures.append("dry_run_is_not_qualification_evidence")

    qualification = report.get("qualification") or {}
    if not isinstance(qualification, Mapping):
        qualification = {}
    if thresholds.require_sealed_provenance:
        campaign = (
            CampaignTrust.from_mapping(trusted_campaign)
            if isinstance(trusted_campaign, Mapping)
            else trusted_campaign
        )
        _require_sealed_campaign(
            report,
            qualification,
            failures,
            trusted_evaluator_keys=trusted_evaluator_keys,
            trusted_campaign=campaign,
        )
    else:
        for key in (
            "blind",
            "oracle_withheld",
            "private_unseen_tasks",
            "independent_evaluator",
            "equal_budget_policy",
        ):
            if qualification.get(key) is not True:
                failures.append(f"qualification_flag_missing:{key}")

    task_results = report.get("task_results") or []
    if not isinstance(task_results, list):
        task_results = []

    task_ids: set[str] = set()
    declared_repositories: set[str] = set()
    categories: set[str] = set()
    category_tasks: dict[str, set[str]] = defaultdict(set)
    trials_by_task: dict[str, set[int]] = defaultdict(set)
    flat_results: list[Mapping[str, Any]] = []
    repository_hashes_by_task: dict[str, set[str]] = defaultdict(set)
    prompt_hashes_by_task: dict[str, set[str]] = defaultdict(set)
    seen_groups: set[tuple[str, int]] = set()
    group_results: dict[tuple[str, int], dict[str, Mapping[str, Any]]] = {}

    required_agents = (
        thresholds.nexus_agent,
        thresholds.direct_baseline_agent,
        thresholds.claude_agent,
    )
    sealed_budget = qualification.get("budget_policy") or {}
    declared_budget = sealed_budget.get("declared") if isinstance(sealed_budget, Mapping) else {}
    if not isinstance(declared_budget, Mapping):
        declared_budget = {}
    try:
        maximum_wall_time = float(declared_budget.get("maximum_wall_time_seconds_per_run", 0))
    except (TypeError, ValueError):
        maximum_wall_time = 0.0
    try:
        maximum_interventions = int(declared_budget.get("maximum_human_interventions_per_run", -1))
    except (TypeError, ValueError):
        maximum_interventions = -1

    for group in task_results:
        if not isinstance(group, Mapping):
            continue
        task_id = str(group.get("task_id", "")).strip()
        category = str(group.get("category", "unspecified")).strip()
        trial = int(group.get("trial") or 0)
        if not task_id or trial <= 0:
            failures.append("invalid_task_group_identity")
            continue
        group_key = (task_id, trial)
        group_budget = group.get("budget") or {}
        if not isinstance(group_budget, Mapping):
            failures.append(f"task_budget_missing:{task_id}:{trial}")
            group_budget = {}
        try:
            agent_timeout = float(group_budget.get("agent_timeout_seconds", 0))
            verification_timeout = float(group_budget.get("verification_timeout_seconds", 0))
        except (TypeError, ValueError):
            agent_timeout = 0.0
            verification_timeout = 0.0
        if agent_timeout <= 0 or verification_timeout <= 0:
            failures.append(f"task_budget_invalid:{task_id}:{trial}")
        if maximum_wall_time > 0 and agent_timeout > maximum_wall_time:
            failures.append(f"task_budget_exceeds_seal:{task_id}:{trial}")
        if group_key in seen_groups:
            failures.append(f"duplicate_task_trial:{task_id}:{trial}")
            continue
        seen_groups.add(group_key)
        task_ids.add(task_id)
        repository_id = str(group.get("repository_id", "")).strip()
        if not repository_id:
            failures.append(f"repository_id_missing:{task_id}")
        else:
            declared_repositories.add(repository_id)
        categories.add(category)
        category_tasks[category].add(task_id)
        trials_by_task[task_id].add(trial)

        per_agent: dict[str, Mapping[str, Any]] = {}
        for raw_item in group.get("results") or []:
            if not isinstance(raw_item, Mapping):
                continue
            item = dict(raw_item)
            agent = str(item.get("agent", "")).strip()
            if agent in per_agent:
                failures.append(f"duplicate_agent_result:{task_id}:{trial}:{agent}")
                continue
            per_agent[agent] = item
            item["_category"] = category
            item["_task_id"] = task_id
            item["_trial"] = trial
            flat_results.append(item)
            _validate_observed_metrics(
                item,
                task_id=task_id,
                trial=trial,
                agent=agent,
                maximum_wall_time=maximum_wall_time,
                maximum_interventions=maximum_interventions,
                failures=failures,
            )
            provenance = item.get("provenance") or {}
            if isinstance(provenance, Mapping):
                repository_hashes_by_task[task_id].add(str(provenance.get("repository_sha256", "")))
                prompt_hashes_by_task[task_id].add(str(provenance.get("prompt_sha256", "")))
                isolation = provenance.get("candidate_isolation") or {}
                if not isinstance(isolation, Mapping) or not all(
                    isolation.get(key) is True
                    for key in (
                        "filesystem_isolation",
                        "network_denied",
                        "network_enforced",
                        "original_repository_unreadable",
                        "oracle_unreadable",
                    )
                ):
                    failures.append(f"candidate_isolation_unproven:{task_id}:{trial}:{agent}")
                telemetry = provenance.get("telemetry") or {}
                if (
                    not isinstance(telemetry, Mapping)
                    or telemetry.get("source") != "evaluator_harness"
                    or telemetry.get("candidate_writable") is not False
                    or not _SHA256_RE.fullmatch(str(telemetry.get("record_sha256", "")))
                ):
                    failures.append(f"evaluator_telemetry_untrusted:{task_id}:{trial}:{agent}")
            else:
                failures.append(f"provenance_missing:{task_id}:{trial}:{agent}")
        group_results[group_key] = per_agent
        for agent in required_agents:
            if agent not in per_agent:
                failures.append(f"missing_agent_result:{task_id}:{trial}:{agent}")

    if len(task_ids) < thresholds.minimum_unique_tasks:
        failures.append(f"unique_tasks:{len(task_ids)}<{thresholds.minimum_unique_tasks}")
    missing_categories = sorted(set(thresholds.required_categories) - categories)
    if missing_categories:
        failures.append("missing_categories:" + ",".join(missing_categories))

    for category in thresholds.required_categories:
        task_count = len(category_tasks.get(category, set()))
        if task_count < thresholds.minimum_tasks_per_category:
            failures.append(
                f"category_tasks:{category}:{task_count}<{thresholds.minimum_tasks_per_category}"
            )

    incomplete = sorted(
        task
        for task, trials in trials_by_task.items()
        if len(trials) < thresholds.minimum_trials_per_task
    )
    if incomplete:
        failures.append(f"incomplete_trials:{len(incomplete)}")

    unique_repository_hashes: set[str] = set()
    unique_task_fingerprints: set[tuple[str, str]] = set()
    for task_id in sorted(task_ids):
        repo_hashes = {
            item.lower() for item in repository_hashes_by_task.get(task_id, set()) if item
        }
        prompt_hashes = {item.lower() for item in prompt_hashes_by_task.get(task_id, set()) if item}
        if len(repo_hashes) != 1:
            failures.append(f"unmatched_repository_provenance:{task_id}")
        elif not _SHA256_RE.fullmatch(next(iter(repo_hashes))):
            failures.append(f"invalid_repository_provenance:{task_id}")
        else:
            unique_repository_hashes.update(repo_hashes)
        if len(prompt_hashes) != 1:
            failures.append(f"unmatched_prompt_provenance:{task_id}")
        elif not _SHA256_RE.fullmatch(next(iter(prompt_hashes))):
            failures.append(f"invalid_prompt_provenance:{task_id}")
        if (
            len(repo_hashes) == 1
            and len(prompt_hashes) == 1
            and _SHA256_RE.fullmatch(next(iter(repo_hashes)))
            and _SHA256_RE.fullmatch(next(iter(prompt_hashes)))
        ):
            fingerprint = (next(iter(repo_hashes)), next(iter(prompt_hashes)))
            if fingerprint in unique_task_fingerprints:
                failures.append(f"duplicate_task_content:{task_id}")
            unique_task_fingerprints.add(fingerprint)

    if len(unique_repository_hashes) < thresholds.minimum_unique_repositories:
        failures.append(
            "unique_repositories:"
            f"{len(unique_repository_hashes)}<{thresholds.minimum_unique_repositories}"
        )

    agents = {name: _agent_metrics(flat_results, name) for name in required_agents}
    expected_runs = len(seen_groups)
    for name, metrics in agents.items():
        if metrics.runs != expected_runs:
            failures.append(f"agent_runs:{name}:{metrics.runs}!={expected_runs}")
        if metrics.available_runs != metrics.runs:
            failures.append(f"agent_unavailable:{name}")
        if metrics.completed_runs != metrics.runs:
            failures.append(f"agent_incomplete:{name}")
        if not metrics.provenance_fingerprints:
            failures.append(f"agent_provenance_missing:{name}")

    provenance_sets = [set(agents[name].provenance_fingerprints) for name in required_agents]
    if any(provenance_sets[i] == provenance_sets[j] for i in range(3) for j in range(i + 1, 3)):
        failures.append("agent_provenance_not_distinct")

    nexus, baseline, claude = _validate_agent_identities(
        agents, thresholds=thresholds, failures=failures
    )
    claude_margin = nexus.verified_rate - claude.verified_rate
    direct_uplift = _safe_ratio(nexus.verified_rate, baseline.verified_rate)
    cost_ratio = (
        _safe_ratio(nexus.median_cost_usd, claude.median_cost_usd)
        if nexus.median_cost_usd is not None and claude.median_cost_usd is not None
        else None
    )
    duration_ratio = _safe_ratio(nexus.median_duration_ms, claude.median_duration_ms)
    intervention_ratio = None
    if (
        nexus.median_human_interventions is not None
        and claude.median_human_interventions is not None
    ):
        if nexus.median_human_interventions == 0 and claude.median_human_interventions == 0:
            intervention_ratio = 1.0
        else:
            intervention_ratio = _safe_ratio(
                nexus.median_human_interventions,
                claude.median_human_interventions,
            )

    paired_wins, paired_losses, paired_ties = _paired_outcomes(
        group_results,
        nexus_agent=thresholds.nexus_agent,
        claude_agent=thresholds.claude_agent,
    )

    if nexus.verified_rate < thresholds.minimum_nexus_verified_rate:
        failures.append("nexus_verified_rate_below_threshold")
    if claude_margin < thresholds.minimum_claude_margin:
        failures.append("claude_margin_below_threshold")
    if direct_uplift is None or direct_uplift < thresholds.minimum_direct_uplift:
        failures.append("direct_model_uplift_below_threshold")
    if paired_wins - paired_losses < thresholds.minimum_paired_net_wins:
        failures.append("paired_net_wins_below_threshold")
    if nexus.false_success_rate > thresholds.maximum_false_success_rate:
        failures.append("nexus_false_success_above_threshold")
    if nexus.false_success_rate > claude.false_success_rate:
        failures.append("nexus_false_success_worse_than_claude")
    if nexus.unexpected_change_rate > thresholds.maximum_unexpected_change_rate:
        failures.append("nexus_unexpected_changes_above_threshold")
    if nexus.unexpected_change_rate > claude.unexpected_change_rate:
        failures.append("nexus_unexpected_changes_worse_than_claude")
    if duration_ratio is None or duration_ratio > thresholds.maximum_duration_ratio_to_claude:
        failures.append("nexus_duration_worse_than_threshold")
    if thresholds.require_cost_metrics and cost_ratio is None:
        failures.append("cost_metrics_incomplete")
    elif cost_ratio is not None and cost_ratio > thresholds.maximum_cost_ratio_to_claude:
        failures.append("nexus_cost_ratio_above_threshold")
    if thresholds.require_token_metrics and any(
        metrics.median_input_tokens is None or metrics.median_output_tokens is None
        for metrics in agents.values()
    ):
        failures.append("token_metrics_incomplete")
    if thresholds.require_intervention_metrics and intervention_ratio is None:
        failures.append("intervention_metrics_incomplete")
    elif (
        intervention_ratio is not None
        and intervention_ratio > thresholds.maximum_intervention_ratio_to_claude
    ):
        failures.append("nexus_intervention_ratio_above_threshold")

    category_metrics = _category_metrics(flat_results, thresholds=thresholds)
    _validate_category_thresholds(category_metrics, thresholds=thresholds, failures=failures)

    metrics = {
        "unique_tasks": len(task_ids),
        "unique_repositories": len(unique_repository_hashes),
        "declared_repository_ids": len(declared_repositories),
        "unique_task_fingerprints": len(unique_task_fingerprints),
        "categories": sorted(categories),
        "agents": {name: value.to_dict() for name, value in agents.items()},
        "per_category": category_metrics,
        "paired": {
            "wins": paired_wins,
            "losses": paired_losses,
            "ties": paired_ties,
            "net_wins": paired_wins - paired_losses,
        },
        "claude_verified_margin": round(claude_margin, 6),
        "direct_model_uplift": direct_uplift,
        "cost_ratio_to_claude": cost_ratio,
        "duration_ratio_to_claude": duration_ratio,
        "intervention_ratio_to_claude": intervention_ratio,
    }
    qualified = not failures
    return SuperiorityEvaluation(
        qualified=qualified,
        status="PASS" if qualified else "INSUFFICIENT_OR_FAILED_EVIDENCE",
        claim=(
            "Noryx exceeded every configured Claude Code superiority gate on this sealed task set."
            if qualified
            else "No Claude Code superiority claim is supported by this report."
        ),
        metrics=metrics,
        failures=tuple(dict.fromkeys(failures)),
    )
