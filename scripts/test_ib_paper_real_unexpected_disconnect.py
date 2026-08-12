"""Real IB paper unexpected-connectivity recovery test.

IMPORTANT:

This harness contains NO order-submission path.

It tests the real IB system-message lifecycle:

    healthy TWS/IB connection
        ↓
    user temporarily interrupts INTERNET connectivity
        ↓
    TWS reports IB error 1100
        ↓
    BTS kill switch activates
        ↓
    execution readiness is blocked
        ↓
    internet connectivity is restored
        ↓
    TWS reports IB error 1101 or 1102
        ↓
    kill switch remains active
        ↓
    fresh broker position snapshot confirms FLAT
        ↓
    operator explicitly types RESET
        ↓
    kill switch resets
        ↓
    BTS readiness returns

Do NOT close TWS for this test. An IB 1100 event concerns
connectivity between TWS and IB servers, not a normal local
BTS-to-TWS disconnect.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import time

from app.execution_ledger import ExecutionLedger
from app.ib_api_position_app import IBApiPositionApp
from app.ib_broker_client import IBBrokerClient
from app.ib_connection_manager import IBConnectionManager
from app.ib_error_handler import IBErrorSeverity
from app.ib_trading_readiness import (
    IBReadinessFailure,
    IBTradingReadiness,
)
from app.kill_switch import KillSwitch
from app.trading_controls import TradingControls


HOST = "127.0.0.1"
PORT = 7497
CLIENT_ID = 1

CONNECTION_TIMEOUT_SECONDS = 10.0
LOSS_TIMEOUT_SECONDS = 120.0
RESTORE_TIMEOUT_SECONDS = 180.0
POLL_INTERVAL_SECONDS = 0.05

RESET_CONFIRMATION = "RESET"


class ObservingIBApiPositionApp(
    IBApiPositionApp
):
    """IB application that records observed error classifications.

    This subclass exists only for the live diagnostic harness.
    Production behavior remains in IBApiPositionApp.
    """

    def __init__(
        self,
        broker_client: IBBrokerClient,
        *,
        execution_ledger: ExecutionLedger,
        kill_switch: KillSwitch,
    ) -> None:
        """Create an observing API application."""

        super().__init__(
            broker_client,
            execution_ledger=execution_ledger,
            kill_switch=kill_switch,
        )

        self._observed_error_codes: list[int] = []

        self._observed_error_severities: list[
            IBErrorSeverity
        ] = []

    @property
    def observed_error_codes(
        self,
    ) -> tuple[int, ...]:
        """Return all classified IB error codes observed."""

        return tuple(
            self._observed_error_codes
        )

    @property
    def observed_error_severities(
        self,
    ) -> tuple[IBErrorSeverity, ...]:
        """Return all classified error severities observed."""

        return tuple(
            self._observed_error_severities
        )

    def error(
        self,
        reqId: int,
        errorTime: int,
        errorCode: int,
        errorString: str,
        advancedOrderRejectJson="",
    ) -> None:
        """Observe one real IB error callback."""

        super().error(
            reqId,
            errorTime,
            errorCode,
            errorString,
            advancedOrderRejectJson,
        )

        result = self.last_error_result

        if result is None:
            return

        self._observed_error_codes.append(
            errorCode
        )

        self._observed_error_severities.append(
            result.severity
        )

    def has_seen_error_code(
        self,
        error_code: int,
    ) -> bool:
        """Return True when a specific IB code was observed."""

        return (
            error_code
            in self._observed_error_codes
        )

    def has_seen_connection_restore(
        self,
    ) -> bool:
        """Return True after IB reports 1101 or 1102."""

        return (
            self.has_seen_error_code(1101)
            or self.has_seen_error_code(1102)
        )


@dataclass(frozen=True, slots=True)
class IBRealUnexpectedDisconnectResult:
    """Immutable result of the real connectivity-loss test."""

    initially_ready: bool
    initial_position_count: int

    saw_error_1100: bool
    kill_switch_after_loss: bool
    readiness_after_loss: bool
    execution_blocked_after_loss: bool

    saw_restore_1101_or_1102: bool
    kill_switch_after_restore: bool
    readiness_after_restore: bool
    execution_blocked_after_restore: bool

    post_restore_position_count: int

    operator_reset_confirmed: bool
    kill_switch_after_reset: bool
    final_readiness: bool

    @property
    def successful(self) -> bool:
        """Return True when the complete real test passed."""

        return (
            self.initially_ready
            and self.initial_position_count == 0
            and self.saw_error_1100
            and self.kill_switch_after_loss
            and not self.readiness_after_loss
            and self.execution_blocked_after_loss
            and self.saw_restore_1101_or_1102
            and self.kill_switch_after_restore
            and not self.readiness_after_restore
            and self.execution_blocked_after_restore
            and self.post_restore_position_count == 0
            and self.operator_reset_confirmed
            and not self.kill_switch_after_reset
            and self.final_readiness
        )


def wait_until(
    condition: Callable[[], bool],
    *,
    description: str,
    timeout_seconds: float,
    poll_interval_seconds: float = POLL_INTERVAL_SECONDS,
) -> None:
    """Wait for one real asynchronous IB condition."""

    if not callable(
        condition
    ):
        raise TypeError(
            "'condition' must be callable."
        )

    if (
        not isinstance(description, str)
        or not description.strip()
    ):
        raise ValueError(
            "'description' must be a non-empty string."
        )

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(
            timeout_seconds,
            (int, float),
        )
        or timeout_seconds <= 0
    ):
        raise ValueError(
            "'timeout_seconds' must be positive."
        )

    if (
        isinstance(poll_interval_seconds, bool)
        or not isinstance(
            poll_interval_seconds,
            (int, float),
        )
        or poll_interval_seconds <= 0
    ):
        raise ValueError(
            "'poll_interval_seconds' must be positive."
        )

    deadline = (
        time.monotonic()
        + float(
            timeout_seconds
        )
    )

    while not condition():
        if (
            time.monotonic()
            >= deadline
        ):
            raise TimeoutError(
                f"Timed out waiting for "
                f"{description.strip()}."
            )

        time.sleep(
            float(
                poll_interval_seconds
            )
        )


def require_flat_account(
    broker_client: IBBrokerClient,
) -> int:
    """Require a completed broker snapshot with no positions."""

    if not isinstance(
        broker_client,
        IBBrokerClient,
    ):
        raise TypeError(
            "'broker_client' must be an IBBrokerClient."
        )

    positions = (
        broker_client.get_raw_positions()
    )

    if positions:
        raise RuntimeError(
            "Real unexpected-disconnect test "
            "requires a completely flat paper account."
        )

    return len(
        positions
    )


def require_execution_blocked(
    readiness: IBTradingReadiness,
) -> bool:
    """Prove that mandatory readiness blocks execution."""

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
            "Safety violation: BTS remained ready "
            "during an IB connectivity emergency."
        )

    if (
        IBReadinessFailure.KILL_SWITCH_ACTIVE
        not in result.failures
    ):
        raise RuntimeError(
            "KillSwitchActive was not reported "
            "during the connectivity emergency."
        )

    try:
        readiness.require_ready(
            positions_reconciled=True,
            execution_state_clear=True,
        )

    except RuntimeError:
        return True

    raise RuntimeError(
        "Safety violation: require_ready() "
        "did not block execution."
    )


def confirm_operator_reset(
    *,
    input_function: Callable[[str], str] = input,
) -> bool:
    """Require explicit human confirmation before resetting."""

    if not callable(
        input_function
    ):
        raise TypeError(
            "'input_function' must be callable."
        )

    response = input_function(
        "\nType RESET to confirm that the account is flat "
        "and allow BTS kill-switch reset: "
    )

    return (
        isinstance(response, str)
        and response.strip().upper()
        == RESET_CONFIRMATION
    )


def run_real_unexpected_disconnect_test(
    *,
    ledger_path: str | Path,
    input_function: Callable[[str], str] = input,
) -> IBRealUnexpectedDisconnectResult:
    """Run the real TWS/IB unexpected-connectivity test."""

    broker_client = IBBrokerClient()

    execution_ledger = ExecutionLedger(
        ledger_path
    )

    kill_switch = KillSwitch()

    trading_controls = TradingControls()

    trading_controls.resume()

    app = ObservingIBApiPositionApp(
        broker_client,
        execution_ledger=execution_ledger,
        kill_switch=kill_switch,
    )

    manager = IBConnectionManager(
        app,
        host=HOST,
        port=PORT,
        client_id=CLIENT_ID,
        connection_timeout_seconds=(
            CONNECTION_TIMEOUT_SECONDS
        ),
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

    try:
        # -----------------------------------------------------
        # Establish healthy baseline.
        # -----------------------------------------------------

        manager.connect()

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="initial IB position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        initial_position_count = (
            require_flat_account(
                broker_client
            )
        )

        initial_readiness = (
            readiness.require_ready(
                positions_reconciled=True,
                execution_state_clear=True,
            )
        )

        print()
        print(
            "INITIAL STATE VERIFIED: BTS is READY and "
            "the paper account is FLAT."
        )
        print()
        print(
            "NOW TEMPORARILY INTERRUPT THIS COMPUTER'S "
            "INTERNET CONNECTION."
        )
        print(
            "IMPORTANT: Leave TWS OPEN."
        )
        print(
            "Do NOT disable the TWS API socket and "
            "do NOT close TWS."
        )
        print()
        print(
            "BTS is waiting for real IB system message 1100..."
        )

        # -----------------------------------------------------
        # Wait for real server-connectivity loss.
        # -----------------------------------------------------

        wait_until(
            lambda: (
                app.has_seen_error_code(
                    1100
                )
                and kill_switch.active
            ),
            description=(
                "real IB error 1100 connectivity loss"
            ),
            timeout_seconds=(
                LOSS_TIMEOUT_SECONDS
            ),
        )

        loss_readiness = readiness.evaluate(
            positions_reconciled=True,
            execution_state_clear=True,
        )

        blocked_after_loss = (
            require_execution_blocked(
                readiness
            )
        )

        print()
        print(
            "IB 1100 RECEIVED."
        )
        print(
            "Kill switch is ACTIVE and execution "
            "is BLOCKED."
        )
        print()
        print(
            "RESTORE THE COMPUTER'S INTERNET CONNECTION NOW."
        )
        print(
            "Leave TWS running and allow it to reconnect "
            "to IB automatically."
        )
        print()
        print(
            "BTS is waiting for IB 1101 or 1102..."
        )

        # -----------------------------------------------------
        # Wait for real IB server-connectivity restoration.
        # -----------------------------------------------------

        wait_until(
            lambda: (
                app.has_seen_connection_restore()
            ),
            description=(
                "real IB connectivity restoration "
                "1101 or 1102"
            ),
            timeout_seconds=(
                RESTORE_TIMEOUT_SECONDS
            ),
        )

        restore_readiness = (
            readiness.evaluate(
                positions_reconciled=True,
                execution_state_clear=True,
            )
        )

        blocked_after_restore = (
            require_execution_blocked(
                readiness
            )
        )

        if not kill_switch.active:
            raise RuntimeError(
                "Safety violation: connectivity restoration "
                "automatically cleared the kill switch."
            )

        # -----------------------------------------------------
        # Fresh post-recovery broker reconciliation.
        # -----------------------------------------------------

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description=(
                "post-restoration IB position snapshot"
            ),
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        post_restore_position_count = (
            require_flat_account(
                broker_client
            )
        )

        print()
        print(
            "POST-RESTORE POSITION SNAPSHOT: FLAT."
        )
        print(
            "Kill switch is intentionally STILL ACTIVE."
        )

        operator_reset_confirmed = (
            confirm_operator_reset(
                input_function=input_function
            )
        )

        if not operator_reset_confirmed:
            raise RuntimeError(
                "Operator did not confirm kill-switch reset."
            )

        kill_switch.reset()

        final_readiness = (
            readiness.require_ready(
                positions_reconciled=True,
                execution_state_clear=True,
            )
        )

        return IBRealUnexpectedDisconnectResult(
            initially_ready=(
                initial_readiness.ready
            ),
            initial_position_count=(
                initial_position_count
            ),

            saw_error_1100=(
                app.has_seen_error_code(
                    1100
                )
            ),
            kill_switch_after_loss=True,
            readiness_after_loss=(
                loss_readiness.ready
            ),
            execution_blocked_after_loss=(
                blocked_after_loss
            ),

            saw_restore_1101_or_1102=(
                app.has_seen_connection_restore()
            ),
            kill_switch_after_restore=True,
            readiness_after_restore=(
                restore_readiness.ready
            ),
            execution_blocked_after_restore=(
                blocked_after_restore
            ),

            post_restore_position_count=(
                post_restore_position_count
            ),

            operator_reset_confirmed=(
                operator_reset_confirmed
            ),
            kill_switch_after_reset=(
                kill_switch.active
            ),
            final_readiness=(
                final_readiness.ready
            ),
        )

    finally:
        manager.disconnect()


def print_result(
    result: IBRealUnexpectedDisconnectResult,
) -> None:
    """Print the real emergency recovery result."""

    if not isinstance(
        result,
        IBRealUnexpectedDisconnectResult,
    ):
        raise TypeError(
            "'result' must be an "
            "IBRealUnexpectedDisconnectResult."
        )

    print()
    print(
        "BTS / IB REAL UNEXPECTED DISCONNECT TEST"
    )
    print(
        "========================================"
    )
    print(
        f"Initially ready:               {result.initially_ready}"
    )
    print(
        f"Initial positions:             {result.initial_position_count}"
    )
    print(
        "Saw real IB 1100:             "
        f"{result.saw_error_1100}"
    )
    print(
        "Kill switch after loss:       "
        f"{result.kill_switch_after_loss}"
    )
    print(
        "Execution blocked after loss: "
        f"{result.execution_blocked_after_loss}"
    )
    print(
        "Saw real IB 1101/1102:        "
        f"{result.saw_restore_1101_or_1102}"
    )
    print(
        "Kill switch after restore:    "
        f"{result.kill_switch_after_restore}"
    )
    print(
        "Execution blocked restored:   "
        f"{result.execution_blocked_after_restore}"
    )
    print(
        "Post-restore positions:       "
        f"{result.post_restore_position_count}"
    )
    print(
        "Operator reset confirmed:     "
        f"{result.operator_reset_confirmed}"
    )
    print(
        "Kill switch after reset:      "
        f"{result.kill_switch_after_reset}"
    )
    print(
        f"Final readiness:               {result.final_readiness}"
    )
    print(
        "========================================"
    )

    if result.successful:
        print(
            "RESULT: PASS - real unexpected IB "
            "disconnect remained fail-safe."
        )
    else:
        print(
            "RESULT: FAIL - real unexpected "
            "disconnect validation failed."
        )

    print()


def main() -> int:
    """Run the real unexpected-connectivity test."""

    ledger_path = (
        Path("data")
        / "ib_real_unexpected_disconnect_test.db"
    )

    ledger_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    print()
    print(
        "Starting REAL IB unexpected-disconnect test..."
    )
    print(
        "NO order-submission path exists in this harness."
    )
    print(
        "The paper account must be completely FLAT."
    )

    try:
        result = (
            run_real_unexpected_disconnect_test(
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