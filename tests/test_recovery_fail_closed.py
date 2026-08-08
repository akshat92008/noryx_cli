from __future__ import annotations

from nexus.recovery import (
    FailureCategory,
    FailureKind,
    FailureRecord,
    RecoveryController,
    RecoveryStrategyType,
    StrategyRegistry,
)


def _record() -> FailureRecord:
    return FailureRecord(
        failure_id="fail-closed-1",
        run_id="run-1",
        category=FailureCategory.VERIFICATION,
        kind=FailureKind.TARGETED_TEST_FAILED,
        source_component="test",
        phase="verification",
        summary="targeted test failed",
    )


def test_strategy_without_handler_fails_closed() -> None:
    strategy = StrategyRegistry.get(RecoveryStrategyType.REVISE_PLAN)
    assert strategy.apply(_record(), {}) is False


def test_stop_strategy_is_never_reported_as_applied() -> None:
    strategy = StrategyRegistry.get(RecoveryStrategyType.STOP_BLOCKED)
    assert strategy.apply(_record(), {"apply_strategy": lambda: True}) is False


def test_strategy_requires_affirmative_handler_result() -> None:
    strategy = StrategyRegistry.get(RecoveryStrategyType.REVISE_PLAN)
    assert strategy.apply(_record(), {"replan": lambda: None}) is False
    assert strategy.apply(_record(), {"replan": lambda: {"success": True}}) is True


def test_controller_counts_only_real_repairs(monkeypatch) -> None:
    controller = RecoveryController()
    strategy = StrategyRegistry.get(RecoveryStrategyType.REVISE_PLAN)
    monkeypatch.setattr(controller.signature_engine, "select_strategy", lambda diagnosis: strategy)

    applied, selected = controller.diagnose_and_recover(_record(), {})
    assert applied is False
    assert selected is strategy
    assert controller.repairs_done == 0

    applied, _ = controller.diagnose_and_recover(
        _record(),
        {"replan": lambda **_: {"applied": True}},
    )
    assert applied is True
    assert controller.repairs_done == 1
