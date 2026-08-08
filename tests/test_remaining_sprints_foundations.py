import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from nexus.autonomy import ProjectScheduler, ProjectService, ProjectState
from nexus.autonomy.projects import AutonomyStore
from nexus.enterprise import (
    ApprovalWorkflowService,
    AuthorizationService,
    BudgetGovernanceService,
    BudgetLimit,
    ComplianceExportService,
    EnterpriseAuditService,
    EnterpriseStore,
    IdentityService,
    PolicyEngine,
    PolicyRule,
    Role,
    SecretBroker,
)
from nexus.performance import (
    BoundedEventHistory,
    ContentHashCache,
    LowResourceProfile,
    PerformanceBudget,
    PerformanceProfiler,
    RegressionGate,
)
from nexus.release.qualification import (
    DEFAULT_RELEASE_SCOPE,
    ReleaseQualification,
    RollbackPlan,
    build_supply_chain_evidence,
)


def test_enterprise_authorization_is_deny_by_default(tmp_path):
    auth = AuthorizationService(IdentityService(EnterpriseStore(tmp_path)))

    assert auth.is_allowed("missing", "read_repository") is False


@pytest.mark.parametrize(
    "role,permission,expected",
    [
        (Role.VIEWER, "read_repository", True),
        (Role.VIEWER, "approve_edits", False),
        (Role.DEVELOPER, "request_edits", True),
        (Role.AUDITOR, "export_compliance_data", True),
        (Role.ORG_ADMIN, "manage_policies", True),
    ],
)
def test_enterprise_rbac_matrix(tmp_path, role, permission, expected):
    store = EnterpriseStore(tmp_path)
    IdentityService(store).create("u1", "User", roles=(role,))

    assert AuthorizationService(IdentityService(store)).is_allowed("u1", permission) is expected


def test_policy_engine_deny_precedence_and_default_deny(tmp_path):
    store = EnterpriseStore(tmp_path)
    engine = PolicyEngine(store)
    engine.activate_rules([
        PolicyRule("allow_cloud", "allow", {"provider": "openai"}, reason="provider_allowed"),
        PolicyRule("deny_sensitive", "deny", {"data_sensitivity": "restricted"}, reason="restricted_data"),
    ])

    denied = engine.evaluate({"identity_id": "u1", "provider": "openai", "data_sensitivity": "restricted"})
    default_denied = engine.evaluate({"identity_id": "u1", "provider": "unknown"})

    assert denied.allowed is False
    assert denied.effect == "deny"
    assert "restricted_data" in denied.reason_codes
    assert default_denied.allowed is False
    assert "deny_by_default" in default_denied.reason_codes


def test_policy_conditions_support_constraints(tmp_path):
    engine = PolicyEngine(EnterpriseStore(tmp_path))
    engine.activate_rules([
        PolicyRule(
            "sandbox_commands",
            "allow_with_constraints",
            {"tool_capability": "execute_commands"},
            {"require_sandbox": True},
            "commands_need_sandbox",
        )
    ])

    decision = engine.evaluate({"identity_id": "dev", "tool_capability": "execute_commands"})

    assert decision.allowed is True
    assert decision.constraints["require_sandbox"] is True


def test_approval_workflow_prevents_self_approval(tmp_path):
    store = EnterpriseStore(tmp_path)
    identities = IdentityService(store)
    identities.create("requester", "Requester", roles=(Role.APPROVER,))
    service = ApprovalWorkflowService(store, AuthorizationService(identities))
    request = service.request("requester", "destructive-action", "high")

    with pytest.raises(PermissionError):
        service.decide(request.request_id, "requester", "approved")


def test_approval_workflow_requires_approver_permission(tmp_path):
    store = EnterpriseStore(tmp_path)
    identities = IdentityService(store)
    identities.create("requester", "Requester")
    identities.create("viewer", "Viewer", roles=(Role.VIEWER,))
    service = ApprovalWorkflowService(store, AuthorizationService(identities))
    request = service.request("requester", "edit", "medium")

    with pytest.raises(PermissionError):
        service.decide(request.request_id, "viewer", "approved")


def test_secret_broker_scopes_and_redacts(tmp_path):
    store = EnterpriseStore(tmp_path)
    identities = IdentityService(store)
    identities.create("admin", "Admin", roles=(Role.ORG_ADMIN,))
    broker = SecretBroker(store, AuthorizationService(identities))
    broker.put("provider_key", "sk-secret", project_id="p1", provider="openai", purpose="model")

    assert broker.request("provider_key", identity_id="admin", project_id="p1", provider="openai", purpose="model") == "sk-secret"
    assert broker.list_redacted("p1")[0]["value"] == "[REDACTED]"
    with pytest.raises(PermissionError):
        broker.request("provider_key", identity_id="admin", project_id="p1", provider="anthropic")


def test_budget_governance_hard_limit_and_threshold(tmp_path):
    service = BudgetGovernanceService(EnterpriseStore(tmp_path))
    service.set_limit(BudgetLimit("project", "p1", 10.0, approval_threshold_usd=7.0))

    assert service.charge("project", "p1", 5.0).allowed is True
    assert service.charge("project", "p1", 3.0).effect == "require_approval"
    assert service.charge("project", "p1", 20.0).effect == "deny"


def test_enterprise_audit_chain_detects_tampering(tmp_path):
    store = EnterpriseStore(tmp_path)
    audit = EnterpriseAuditService(store)
    audit.append("policy.decision", "u1", details={"api_key": "secret"})

    assert audit.verify_chain() is True
    data = store.read("audit", {"records": []})
    assert data["records"][0]["details"]["api_key"] == "[REDACTED]"
    data["records"][0]["action"] = "tampered"
    store.write("audit", data)
    assert audit.verify_chain() is False


def test_compliance_export_contains_redacted_credentials_and_audit_status(tmp_path):
    store = EnterpriseStore(tmp_path)
    IdentityService(store).create("admin", "Admin", roles=(Role.ORG_ADMIN,))
    SecretBroker(store).put("token", "super-secret", project_id="p1")
    EnterpriseAuditService(store).append("test", "admin")

    export = ComplianceExportService(store).export()

    assert export["audit_chain_valid"] is True
    assert export["credentials"][0]["value"] == "[REDACTED]"


def test_content_hash_cache_invalidates_on_file_change(tmp_path):
    cache = ContentHashCache(tmp_path / "cache", parser_version="v1", max_entries=2)
    source = tmp_path / "file.py"
    source.write_text("a = 1\n", encoding="utf-8")
    cache.put(source, {"symbols": ["a"]})

    assert cache.get(source)["value"]["symbols"] == ["a"]
    source.write_text("b = 2\n", encoding="utf-8")
    assert cache.get(source) is None


def test_bounded_event_history_caps_memory_shape():
    history = BoundedEventHistory(max_events=3)
    for index in range(5):
        history.append({"index": index})

    snapshot = history.snapshot()
    assert snapshot["retained"] == 3
    assert snapshot["evicted"] == 2
    assert [event["index"] for event in snapshot["events"]] == [2, 3, 4]


def test_performance_regression_gate_flags_budget_excess():
    profiler = PerformanceProfiler()
    profiler.measure("fast", lambda: {"done": True})
    report = profiler.report()

    assert RegressionGate((PerformanceBudget("fast", 10_000),)).evaluate(report) == []
    assert RegressionGate((PerformanceBudget("fast", 0.00001, tolerance_ratio=0),)).evaluate(report)


def test_low_resource_profile_is_conservative():
    profile = LowResourceProfile()

    assert profile.max_parallel_workers == 1
    assert profile.prefer_incremental_indexing is True


def test_autonomy_project_create_plan_pause_resume(tmp_path):
    service = ProjectService(AutonomyStore(tmp_path))
    project = service.create("Modernize auth", requirements=("No regressions",), acceptance_criteria=("Tests pass",))

    assert project.state == ProjectState.PROPOSED
    assert service.plan(project.project_id)["next_milestone"]["milestone_id"] == "m1"
    assert service.transition(project.project_id, ProjectState.APPROVED).state == ProjectState.APPROVED
    assert service.transition(project.project_id, ProjectState.PAUSED).state == ProjectState.PAUSED
    assert service.transition(project.project_id, ProjectState.RUNNING).state == ProjectState.RUNNING
    assert len(service.checkpoints(project.project_id)) >= 3


def test_autonomy_scheduler_respects_dependencies(tmp_path):
    service = ProjectService(AutonomyStore(tmp_path))
    project = service.create("Build feature")

    assert ProjectScheduler().next_milestone(project).milestone_id == "m1"


def test_autonomy_scope_drift_detection_for_unplanned_dependencies(tmp_path):
    service = ProjectService(AutonomyStore(tmp_path))
    project = service.create("Refactor module")

    drift = service.detect_scope_drift(project.project_id, changed_paths=("src/app.py",), new_dependencies=("new-lib",))

    assert drift.detected is True
    assert drift.requires_replan is True


def test_release_scope_channel_and_qualification(tmp_path):
    evidence = build_supply_chain_evidence(dependency_lines=("pytest>=8",), secret_scan_passed=True)
    qualification = ReleaseQualification(
        version="1.0.0",
        scope=DEFAULT_RELEASE_SCOPE,
        supply_chain=evidence,
        rollback_plan=RollbackPlan(safe_version="0.9.0", downgrade_tested=True),
        test_results={"unit": True},
        security_results={"workspace_isolation": "pass"},
    )

    assert DEFAULT_RELEASE_SCOPE.classify("cli_core") == "stable"
    assert qualification.evaluate()["status"] == "pass"


def test_release_qualification_fails_bad_version_and_secret_scan():
    qualification = ReleaseQualification(
        version="1",
        scope=DEFAULT_RELEASE_SCOPE,
        supply_chain=build_supply_chain_evidence(dependency_lines=(), secret_scan_passed=False),
        rollback_plan=RollbackPlan(),
    )

    result = qualification.evaluate()
    assert result["status"] == "fail"
    assert "version_must_be_semver" in result["failures"]
    assert "secret_scan_failed" in result["failures"]


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    repo_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "nexus", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_enterprise_cli_org_policy_budget_audit(tmp_path):
    result = run_cli("org", "--working-dir", str(tmp_path), "--json", "create", "Acme", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    org = json.loads(result.stdout)

    result = run_cli(
        "members",
        "--working-dir",
        str(tmp_path),
        "--json",
        "add",
        "admin",
        "Admin",
        "--org",
        org["organization_id"],
        "--role",
        "org_admin",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli("budgets", "--working-dir", str(tmp_path), "--json", "set", "project", "p1", "5", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    result = run_cli("audit", "--working-dir", str(tmp_path), "--json", "verify", cwd=tmp_path)
    assert json.loads(result.stdout)["valid"] is True


def test_autonomy_performance_release_cli(tmp_path):
    result = run_cli("project", "--working-dir", str(tmp_path), "--json", "create", "Ship v1", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    project = json.loads(result.stdout)

    result = run_cli("project", "--working-dir", str(tmp_path), "--json", "plan", project["project_id"], cwd=tmp_path)
    assert json.loads(result.stdout)["next_milestone"]["milestone_id"] == "m1"

    result = run_cli("performance", "--json", "low-resource", cwd=tmp_path)
    assert json.loads(result.stdout)["max_parallel_workers"] == 1

    result = run_cli("release", "--json", "scope", "cli_core", cwd=tmp_path)
    assert result.stdout.strip() == "stable"


def test_release_candidate_requires_live_and_cross_platform_evidence(tmp_path):
    from nexus.release.qualification import ChannelPolicy

    qualification = ReleaseQualification(
        version="3.3.0",
        supply_chain=build_supply_chain_evidence(
            secret_scan_passed=True,
            artifact_paths=(tmp_path / "missing.whl",),
        ),
        rollback_plan=RollbackPlan(safe_version="uninstalled", downgrade_tested=True),
        test_results={"architecture_health": True},
        security_results={"source_secret_scan": True},
        channel_policy=ChannelPolicy(
            name="release-candidate",
            require_artifact_evidence=True,
            required_test_names=("live_provider_long_horizon", "cross_platform_ci"),
        ),
    )
    failures = qualification.evaluate()["failures"]
    assert "test_evidence_missing:live_provider_long_horizon" in failures
    assert "test_evidence_missing:cross_platform_ci" in failures
    assert any(item.startswith("artifact_missing:") for item in failures)
