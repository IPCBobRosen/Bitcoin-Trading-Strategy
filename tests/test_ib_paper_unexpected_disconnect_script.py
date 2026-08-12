"""Offline tests for unexpected IB connectivity failure handling."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from app.execution_ledger import ExecutionLedger
from app.ib_api_position_app import IBApiPositionApp
from app.ib_broker_client import IBBrokerClient
from app.ib_error_handler import IBErrorSeverity
from app.ib_trading_readiness import (
    IBReadinessFailure,
    IBTradingReadiness,
)
from app.kill_switch import KillSwitch
from app.trading_controls import TradingControls

from scripts.test_ib_paper_unexpected_disconnect import (
    IBUnexpectedDisconnectResult,
    SIMULATED_NEXT_VALID_ID,
    complete_empty_position_snapshot,
    print_result,
    require_execution_blocked,
    run_unexpected_disconnect_simulation,
)


def create_success_result() -> IBUnexpectedDisconnectResult:
    """Create one fully successful emergency lifecycle result."""

    return IBUnexpectedDisconnectResult(
        initially_ready=True,
        loss_severity=IBErrorSeverity.CONNECTION_LOST,
        kill_switch_after_loss=True,
        ready_after_loss=False,
        execution_blocked_after_loss=True,
        restore_severity=IBErrorSeverity.CONNECTION_RESTORED,
        kill_switch_after_restore=True,
        ready_after_restore=False,
        execution_blocked_after_restore=True,
        kill_switch_after_manual_reset=False,
        ready_after_manual_reset=True,
    )


def create_ready_environment(
    tmp_path,
):
    """Create a fully ready offline BTS IB environment."""

    broker_client = IBBrokerClient()

    ledger = ExecutionLedger(
        tmp_path
        / "ledger.db"
    )

    kill_switch = KillSwitch()

    controls = TradingControls()

    controls.resume()

    app = IBApiPositionApp(
        broker_client,
        execution_ledger=ledger,
        kill_switch=kill_switch,
    )

    app.nextValidId(
        SIMULATED_NEXT_VALID_ID
    )

    complete_empty_position_snapshot(
        app
    )

    readiness = IBTradingReadiness(
        api_ready=app.api_ready,
        order_id_allocator=(
            app.order_id_allocator
        ),
        broker_client=broker_client,
        trading_controls=controls,
        kill_switch=kill_switch,
    )

    return (
        app,
        broker_client,
        ledger,
        kill_switch,
        controls,
        readiness,
    )


def test_success_result_is_successful() -> None:
    """Complete emergency lifecycle should report success."""

    assert (
        create_success_result().successful
        is True
    )


def test_loss_must_activate_kill_switch() -> None:
    """Failure state is invalid if emergency protection remains off."""

    result = create_success_result()

    changed = IBUnexpectedDisconnectResult(
        initially_ready=result.initially_ready,
        loss_severity=result.loss_severity,
        kill_switch_after_loss=False,
        ready_after_loss=result.ready_after_loss,
        execution_blocked_after_loss=(
            result.execution_blocked_after_loss
        ),
        restore_severity=result.restore_severity,
        kill_switch_after_restore=(
            result.kill_switch_after_restore
        ),
        ready_after_restore=result.ready_after_restore,
        execution_blocked_after_restore=(
            result.execution_blocked_after_restore
        ),
        kill_switch_after_manual_reset=(
            result.kill_switch_after_manual_reset
        ),
        ready_after_manual_reset=(
            result.ready_after_manual_reset
        ),
    )

    assert changed.successful is False


def test_loss_must_revoke_readiness() -> None:
    """BTS must not remain ready after IB 1100."""

    result = create_success_result()

    changed = IBUnexpectedDisconnectResult(
        initially_ready=result.initially_ready,
        loss_severity=result.loss_severity,
        kill_switch_after_loss=True,
        ready_after_loss=True,
        execution_blocked_after_loss=True,
        restore_severity=result.restore_severity,
        kill_switch_after_restore=True,
        ready_after_restore=False,
        execution_blocked_after_restore=True,
        kill_switch_after_manual_reset=False,
        ready_after_manual_reset=True,
    )

    assert changed.successful is False


def test_restoration_must_not_clear_kill_switch() -> None:
    """IB 1102 alone must never resume BTS."""

    result = create_success_result()

    changed = IBUnexpectedDisconnectResult(
        initially_ready=result.initially_ready,
        loss_severity=result.loss_severity,
        kill_switch_after_loss=True,
        ready_after_loss=False,
        execution_blocked_after_loss=True,
        restore_severity=result.restore_severity,
        kill_switch_after_restore=False,
        ready_after_restore=False,
        execution_blocked_after_restore=True,
        kill_switch_after_manual_reset=False,
        ready_after_manual_reset=True,
    )

    assert changed.successful is False


def test_restoration_must_not_restore_readiness_automatically() -> None:
    """Connectivity restoration is not trading authorization."""

    result = create_success_result()

    changed = IBUnexpectedDisconnectResult(
        initially_ready=result.initially_ready,
        loss_severity=result.loss_severity,
        kill_switch_after_loss=True,
        ready_after_loss=False,
        execution_blocked_after_loss=True,
        restore_severity=result.restore_severity,
        kill_switch_after_restore=True,
        ready_after_restore=True,
        execution_blocked_after_restore=True,
        kill_switch_after_manual_reset=False,
        ready_after_manual_reset=True,
    )

    assert changed.successful is False


def test_manual_reset_must_clear_kill_switch() -> None:
    """Explicit operator recovery should eventually clear emergency."""

    result = create_success_result()

    changed = IBUnexpectedDisconnectResult(
        initially_ready=result.initially_ready,
        loss_severity=result.loss_severity,
        kill_switch_after_loss=True,
        ready_after_loss=False,
        execution_blocked_after_loss=True,
        restore_severity=result.restore_severity,
        kill_switch_after_restore=True,
        ready_after_restore=False,
        execution_blocked_after_restore=True,
        kill_switch_after_manual_reset=True,
        ready_after_manual_reset=True,
    )

    assert changed.successful is False


def test_manual_reset_must_restore_readiness() -> None:
    """Recovery is incomplete until readiness returns."""

    result = create_success_result()

    changed = IBUnexpectedDisconnectResult(
        initially_ready=result.initially_ready,
        loss_severity=result.loss_severity,
        kill_switch_after_loss=True,
        ready_after_loss=False,
        execution_blocked_after_loss=True,
        restore_severity=result.restore_severity,
        kill_switch_after_restore=True,
        ready_after_restore=False,
        execution_blocked_after_restore=True,
        kill_switch_after_manual_reset=False,
        ready_after_manual_reset=False,
    )

    assert changed.successful is False


def test_result_is_immutable() -> None:
    """Emergency result should not be mutable."""

    result = create_success_result()

    with pytest.raises(
        (FrozenInstanceError, AttributeError),
    ):
        result.ready_after_loss = True  # type: ignore[misc]


def test_completed_empty_snapshot_is_complete(
    tmp_path,
) -> None:
    """Offline snapshot helper should establish known-flat state."""

    app, broker_client, *_ = (
        create_ready_environment(
            tmp_path
        )
    )

    assert (
        broker_client.snapshot_complete
        is True
    )

    assert (
        broker_client.get_raw_positions()
        == ()
    )

    assert app.api_ready.ready is True


def test_snapshot_helper_requires_correct_app() -> None:
    """Snapshot helper requires production IB app."""

    with pytest.raises(
        TypeError,
        match="'app'",
    ):
        complete_empty_position_snapshot(
            object()  # type: ignore[arg-type]
        )


def test_initial_environment_is_ready(
    tmp_path,
) -> None:
    """Prepared offline environment should initially trade."""

    *_, readiness = (
        create_ready_environment(
            tmp_path
        )
    )

    result = readiness.require_ready(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert result.ready is True


def test_ib_1100_activates_kill_switch(
    tmp_path,
) -> None:
    """Official connection-loss code must trip emergency protection."""

    (
        app,
        _,
        _,
        kill_switch,
        _,
        _,
    ) = create_ready_environment(
        tmp_path
    )

    app.error(
        reqId=-1,
        errorTime=1770000000,
        errorCode=1100,
        errorString="Connectivity lost.",
    )

    assert kill_switch.active is True

    assert app.last_error_result is not None

    assert (
        app.last_error_result.severity
        is IBErrorSeverity.CONNECTION_LOST
    )


def test_ib_1100_blocks_readiness(
    tmp_path,
) -> None:
    """Kill switch should immediately remove trading readiness."""

    (
        app,
        _,
        _,
        _,
        _,
        readiness,
    ) = create_ready_environment(
        tmp_path
    )

    app.error(
        reqId=-1,
        errorTime=1770000000,
        errorCode=1100,
        errorString="Connectivity lost.",
    )

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert result.ready is False

    assert (
        IBReadinessFailure.KILL_SWITCH_ACTIVE
        in result.failures
    )


def test_mandatory_gate_rejects_after_1100(
    tmp_path,
) -> None:
    """require_ready must reject during connectivity emergency."""

    (
        app,
        _,
        _,
        _,
        _,
        readiness,
    ) = create_ready_environment(
        tmp_path
    )

    app.error(
        reqId=-1,
        errorTime=1770000000,
        errorCode=1100,
        errorString="Connectivity lost.",
    )

    assert (
        require_execution_blocked(
            readiness
        )
        is True
    )


def test_ib_1102_does_not_reset_kill_switch(
    tmp_path,
) -> None:
    """Connectivity restoration must not automatically resume trading."""

    (
        app,
        _,
        _,
        kill_switch,
        _,
        _,
    ) = create_ready_environment(
        tmp_path
    )

    app.error(
        reqId=-1,
        errorTime=1770000000,
        errorCode=1100,
        errorString="Connectivity lost.",
    )

    app.error(
        reqId=-1,
        errorTime=1770000001,
        errorCode=1102,
        errorString="Connectivity restored.",
    )

    assert kill_switch.active is True

    assert app.last_error_result is not None

    assert (
        app.last_error_result.severity
        is IBErrorSeverity.CONNECTION_RESTORED
    )


def test_ib_1102_remains_not_ready(
    tmp_path,
) -> None:
    """Restoration callback alone must leave execution blocked."""

    (
        app,
        _,
        _,
        _,
        _,
        readiness,
    ) = create_ready_environment(
        tmp_path
    )

    app.error(
        reqId=-1,
        errorTime=1770000000,
        errorCode=1100,
        errorString="Connectivity lost.",
    )

    app.error(
        reqId=-1,
        errorTime=1770000001,
        errorCode=1102,
        errorString="Connectivity restored.",
    )

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert result.ready is False

    assert (
        IBReadinessFailure.KILL_SWITCH_ACTIVE
        in result.failures
    )


def test_manual_reset_restores_ready_state_after_reconciliation(
    tmp_path,
) -> None:
    """Explicit reset should work only after surrounding checks are clear."""

    (
        app,
        _,
        _,
        kill_switch,
        _,
        readiness,
    ) = create_ready_environment(
        tmp_path
    )

    app.error(
        reqId=-1,
        errorTime=1770000000,
        errorCode=1100,
        errorString="Connectivity lost.",
    )

    app.error(
        reqId=-1,
        errorTime=1770000001,
        errorCode=1102,
        errorString="Connectivity restored.",
    )

    kill_switch.reset()

    result = readiness.require_ready(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    assert result.ready is True


def test_manual_reset_does_not_override_position_mismatch(
    tmp_path,
) -> None:
    """Kill reset alone cannot bypass reconciliation."""

    (
        app,
        _,
        _,
        kill_switch,
        _,
        readiness,
    ) = create_ready_environment(
        tmp_path
    )

    app.error(
        reqId=-1,
        errorTime=1770000000,
        errorCode=1100,
        errorString="Connectivity lost.",
    )

    app.error(
        reqId=-1,
        errorTime=1770000001,
        errorCode=1102,
        errorString="Connectivity restored.",
    )

    kill_switch.reset()

    result = readiness.evaluate(
        positions_reconciled=False,
        execution_state_clear=True,
    )

    assert result.ready is False

    assert (
        IBReadinessFailure.POSITIONS_NOT_RECONCILED
        in result.failures
    )


def test_manual_reset_does_not_override_execution_uncertainty(
    tmp_path,
) -> None:
    """Kill reset cannot bypass unresolved executions."""

    (
        app,
        _,
        _,
        kill_switch,
        _,
        readiness,
    ) = create_ready_environment(
        tmp_path
    )

    app.error(
        reqId=-1,
        errorTime=1770000000,
        errorCode=1100,
        errorString="Connectivity lost.",
    )

    app.error(
        reqId=-1,
        errorTime=1770000001,
        errorCode=1102,
        errorString="Connectivity restored.",
    )

    kill_switch.reset()

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=False,
    )

    assert result.ready is False

    assert (
        IBReadinessFailure.EXECUTION_UNCERTAINTY
        in result.failures
    )


def test_full_simulation_passes(
    tmp_path,
) -> None:
    """Complete injected error lifecycle should pass."""

    result = (
        run_unexpected_disconnect_simulation(
            ledger_path=(
                tmp_path
                / "simulation.db"
            )
        )
    )

    assert result.successful is True


def test_print_result_reports_pass(
    capsys,
) -> None:
    """Successful simulation should clearly report PASS."""

    print_result(
        create_success_result()
    )

    output = (
        capsys.readouterr().out
    )

    assert "RESULT: PASS" in output

    assert (
        "Kill switch after restore:"
        in output
    )


def test_print_result_requires_correct_type() -> None:
    """Printer should reject unrelated objects."""

    with pytest.raises(
        TypeError,
        match="'result'",
    ):
        print_result(
            object()  # type: ignore[arg-type]
        )


def test_script_contains_no_execution_client_import() -> None:
    """Emergency simulation must not contain order executor."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_unexpected_disconnect.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    assert (
        "from app.ib_execution_client import"
        not in source
    )


def test_script_contains_no_place_order_call() -> None:
    """Emergency simulation must never place an order."""

    source_path = (
        Path(__file__).parents[1]
        / "scripts"
        / "test_ib_paper_unexpected_disconnect.py"
    )

    source = source_path.read_text(
        encoding="utf-8"
    )

    forbidden_call = (
        "place"
        + "Order("
    )

    assert forbidden_call not in source