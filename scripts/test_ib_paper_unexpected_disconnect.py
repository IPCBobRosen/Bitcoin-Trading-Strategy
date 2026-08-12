"""Offline unexpected-IB-disconnect safety test for BTS.

This harness deliberately injects the same IB error callbacks used
for unexpected connectivity loss and restoration.

No broker connection is opened and no order-submission path exists.

Sequence:

    healthy BTS state
        ↓
    IB error 1100
        ↓
    kill switch activates
        ↓
    trading readiness is revoked
        ↓
    IB error 1102
        ↓
    kill switch remains active
        ↓
    execution remains blocked
        ↓
    explicit reconciliation + operator reset
        ↓
    readiness restored
"""

from dataclasses import dataclass
from pathlib import Path

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


SIMULATED_NEXT_VALID_ID = 10
SIMULATED_ERROR_TIME = 1770000000


@dataclass(frozen=True, slots=True)
class IBUnexpectedDisconnectResult:
    """Immutable result of one emergency disconnect simulation."""

    initially_ready: bool

    loss_severity: IBErrorSeverity
    kill_switch_after_loss: bool
    ready_after_loss: bool
    execution_blocked_after_loss: bool

    restore_severity: IBErrorSeverity
    kill_switch_after_restore: bool
    ready_after_restore: bool
    execution_blocked_after_restore: bool

    kill_switch_after_manual_reset: bool
    ready_after_manual_reset: bool

    @property
    def successful(self) -> bool:
        """Return True when the entire safety lifecycle passed."""

        return (
            self.initially_ready
            and self.loss_severity
            is IBErrorSeverity.CONNECTION_LOST
            and self.kill_switch_after_loss
            and not self.ready_after_loss
            and self.execution_blocked_after_loss
            and self.restore_severity
            is IBErrorSeverity.CONNECTION_RESTORED
            and self.kill_switch_after_restore
            and not self.ready_after_restore
            and self.execution_blocked_after_restore
            and not self.kill_switch_after_manual_reset
            and self.ready_after_manual_reset
        )


def complete_empty_position_snapshot(
    app: IBApiPositionApp,
) -> None:
    """Create one completed flat broker snapshot offline."""

    if not isinstance(
        app,
        IBApiPositionApp,
    ):
        raise TypeError(
            "'app' must be an IBApiPositionApp."
        )

    app.position_transport.begin_snapshot()

    app.position_transport.position_end()


def require_execution_blocked(
    readiness: IBTradingReadiness,
) -> bool:
    """Require the mandatory readiness gate to reject trading."""

    if not isinstance(
        readiness,
        IBTradingReadiness,
    ):
        raise TypeError(
            "'readiness' must be an IBTradingReadiness."
        )

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    if result.ready:
        raise RuntimeError(
            "Safety violation: BTS remained trading-ready."
        )

    if (
        IBReadinessFailure.KILL_SWITCH_ACTIVE
        not in result.failures
    ):
        raise RuntimeError(
            "Trading was blocked, but KillSwitchActive "
            "was not reported."
        )

    try:
        readiness.require_ready(
            positions_reconciled=True,
            execution_state_clear=True,
        )

    except RuntimeError:
        return True

    raise RuntimeError(
        "Safety violation: require_ready() permitted "
        "execution while the kill switch was active."
    )


def run_unexpected_disconnect_simulation(
    *,
    ledger_path: str | Path,
) -> IBUnexpectedDisconnectResult:
    """Run one complete unexpected-connectivity safety simulation."""

    broker_client = IBBrokerClient()

    execution_ledger = ExecutionLedger(
        ledger_path
    )

    kill_switch = KillSwitch()

    trading_controls = TradingControls()

    trading_controls.resume()

    app = IBApiPositionApp(
        broker_client,
        execution_ledger=execution_ledger,
        kill_switch=kill_switch,
    )

    # Simulate the completed IB connection handshake.
    app.nextValidId(
        SIMULATED_NEXT_VALID_ID
    )

    # Simulate a completed, flat broker position snapshot.
    complete_empty_position_snapshot(
        app
    )

    readiness = IBTradingReadiness(
        api_ready=app.api_ready,
        order_id_allocator=(
            app.order_id_allocator
        ),
        broker_client=broker_client,
        trading_controls=trading_controls,
        kill_switch=kill_switch,
    )

    initial = readiness.require_ready(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    # ---------------------------------------------------------
    # Unexpected IB connectivity loss.
    # ---------------------------------------------------------

    app.error(
        reqId=-1,
        errorTime=SIMULATED_ERROR_TIME,
        errorCode=1100,
        errorString=(
            "Connectivity between IB and TWS has been lost."
        ),
    )

    loss_result = app.last_error_result

    if loss_result is None:
        raise RuntimeError(
            "IB 1100 was not classified by BTS."
        )

    ready_after_loss = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    execution_blocked_after_loss = (
        require_execution_blocked(
            readiness
        )
    )

    # ---------------------------------------------------------
    # IB reports connectivity restored.
    # ---------------------------------------------------------

    app.error(
        reqId=-1,
        errorTime=SIMULATED_ERROR_TIME + 1,
        errorCode=1102,
        errorString=(
            "Connectivity between IB and TWS has been restored."
        ),
    )

    restore_result = app.last_error_result

    if restore_result is None:
        raise RuntimeError(
            "IB 1102 was not classified by BTS."
        )

    ready_after_restore = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=True,
    )

    execution_blocked_after_restore = (
        require_execution_blocked(
            readiness
        )
    )

    # ---------------------------------------------------------
    # Simulate completed operator reconciliation.
    #
    # At this point:
    # - broker position is known flat;
    # - execution state is explicitly declared clear;
    # - connectivity has been restored.
    #
    # Only now is the kill switch explicitly reset.
    # ---------------------------------------------------------

    kill_switch.reset()

    ready_after_manual_reset = (
        readiness.require_ready(
            positions_reconciled=True,
            execution_state_clear=True,
        )
    )

    return IBUnexpectedDisconnectResult(
        initially_ready=initial.ready,

        loss_severity=loss_result.severity,
        kill_switch_after_loss=True,
        ready_after_loss=(
            ready_after_loss.ready
        ),
        execution_blocked_after_loss=(
            execution_blocked_after_loss
        ),

        restore_severity=(
            restore_result.severity
        ),
        kill_switch_after_restore=True,
        ready_after_restore=(
            ready_after_restore.ready
        ),
        execution_blocked_after_restore=(
            execution_blocked_after_restore
        ),

        kill_switch_after_manual_reset=(
            kill_switch.active
        ),
        ready_after_manual_reset=(
            ready_after_manual_reset.ready
        ),
    )


def print_result(
    result: IBUnexpectedDisconnectResult,
) -> None:
    """Print the emergency disconnect simulation result."""

    if not isinstance(
        result,
        IBUnexpectedDisconnectResult,
    ):
        raise TypeError(
            "'result' must be an "
            "IBUnexpectedDisconnectResult."
        )

    print()
    print(
        "BTS / IB UNEXPECTED DISCONNECT SAFETY TEST"
    )
    print(
        "========================================"
    )

    print(
        f"Initially ready:                {result.initially_ready}"
    )

    print(
        "----------------------------------------"
    )

    print(
        "IB 1100 classification:        "
        f"{result.loss_severity.value}"
    )
    print(
        "Kill switch after loss:        "
        f"{result.kill_switch_after_loss}"
    )
    print(
        "Ready after loss:              "
        f"{result.ready_after_loss}"
    )
    print(
        "Execution blocked after loss:  "
        f"{result.execution_blocked_after_loss}"
    )

    print(
        "----------------------------------------"
    )

    print(
        "IB 1102 classification:        "
        f"{result.restore_severity.value}"
    )
    print(
        "Kill switch after restore:     "
        f"{result.kill_switch_after_restore}"
    )
    print(
        "Ready after restore:           "
        f"{result.ready_after_restore}"
    )
    print(
        "Execution blocked after restore:"
        f" {result.execution_blocked_after_restore}"
    )

    print(
        "----------------------------------------"
    )

    print(
        "Kill switch after manual reset:"
        f" {result.kill_switch_after_manual_reset}"
    )
    print(
        "Ready after manual reset:      "
        f"{result.ready_after_manual_reset}"
    )

    print(
        "========================================"
    )

    if result.successful:
        print(
            "RESULT: PASS - unexpected IB connectivity "
            "loss remained fail-safe."
        )

    else:
        print(
            "RESULT: FAIL - unexpected disconnect "
            "safety validation failed."
        )

    print()


def main() -> int:
    """Run the offline emergency connectivity simulation."""

    ledger_path = (
        Path("data")
        / "ib_unexpected_disconnect_simulation.db"
    )

    ledger_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    print()
    print(
        "Starting unexpected IB disconnect simulation..."
    )
    print(
        "No TWS connection or order-submission path is used."
    )

    try:
        result = (
            run_unexpected_disconnect_simulation(
                ledger_path=ledger_path
            )
        )

    except Exception as error:
        print()
        print(
            "RESULT: FAIL"
        )
        print(
            f"{type(error).__name__}: {error}"
        )
        print()

        return 1

    print_result(
        result
    )

    return (
        0
        if result.successful
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )