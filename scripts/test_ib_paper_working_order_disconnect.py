"""Real IB paper disconnect test with one working limit order.

Test order:

    BUY 1 MBT
    August 2026
    LIMIT 63250
    CME
    Paper account only

Sequence:

    flat account
        ↓
    submit BUY 1 @ 63250 LMT
        ↓
    wait for ACKNOWLEDGED
        ↓
    verify no fill
        ↓
    interrupt internet while TWS stays open
        ↓
    real IB 1100
        ↓
    kill switch ACTIVE
    execution state UNCERTAIN
    new trading BLOCKED
        ↓
    restore internet
        ↓
    IB 1101 / 1102
        ↓
    kill switch remains ACTIVE
        ↓
    fresh position snapshot must still be FLAT
        ↓
    cancel SAME broker order ID
        ↓
    wait for CANCELLED
        ↓
    fresh position snapshot must remain FLAT
        ↓
    explicit operator RESET
        ↓
    readiness restored

The order is NEVER resubmitted.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
import time

from ibapi.order_cancel import OrderCancel

from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.communications.trade_request import TradeRequest
from app.daily_loss_guard import DailyLossGuard
from app.duplicate_order_guard import DuplicateOrderGuard
from app.execution_ledger import (
    ExecutionLedger,
    ExecutionRecord,
    ExecutionStatus,
)
from app.ib_broker_client import IBBrokerClient
from app.ib_connection_manager import IBConnectionManager
from app.ib_execution_client import (
    IBExecutionClient,
    IBSubmissionResult,
)
from app.ib_order_factory import IBOrderFactory
from app.ib_trading_readiness import (
    IBReadinessFailure,
    IBTradingReadiness,
)
from app.kill_switch import KillSwitch
from app.risk_manager import RiskManager
from app.trading_controls import TradingControls

from scripts.test_ib_paper_real_unexpected_disconnect import (
    ObservingIBApiPositionApp,
)


HOST = "127.0.0.1"
PORT = 7497
CLIENT_ID = 1

SYMBOL = "MBT"
LOCAL_SYMBOL = "MBTQ6"
EXCHANGE = "CME"
TRADING_CLASS = "MBT"
CONTRACT_MONTH = "20260828"

LIMIT_PRICE = Decimal("63250")
QUANTITY = 1

STOP_LOSS_POINTS = Decimal("500")
MAX_DAILY_LOSS = Decimal("1000")

CONNECTION_TIMEOUT_SECONDS = 10.0
WORKING_TIMEOUT_SECONDS = 20.0
LOSS_TIMEOUT_SECONDS = 600.0
RESTORE_TIMEOUT_SECONDS = 180.0
CANCEL_TIMEOUT_SECONDS = 20.0

POLL_INTERVAL_SECONDS = 0.05

ARMING_ARGUMENT = "--confirm-working-order-test"
RESET_CONFIRMATION = "RESET"


@dataclass(frozen=True, slots=True)
class IBWorkingOrderDisconnectResult:
    """Immutable result of the working-order outage test."""

    event_id: str
    broker_order_id: int

    initial_position_count: int
    working_status: ExecutionStatus

    saw_error_1100: bool
    kill_switch_after_loss: bool
    blocked_after_loss: bool

    saw_restore: bool
    kill_switch_after_restore: bool
    blocked_after_restore: bool

    position_before_cancel: int
    cancelled_status: ExecutionStatus
    final_position: int

    operator_reset_confirmed: bool
    final_kill_switch_active: bool
    final_readiness: bool

    @property
    def successful(self) -> bool:
        """Return True when the entire live test passed."""

        return (
            self.initial_position_count == 0
            and self.working_status
            is ExecutionStatus.ACKNOWLEDGED
            and self.saw_error_1100
            and self.kill_switch_after_loss
            and self.blocked_after_loss
            and self.saw_restore
            and self.kill_switch_after_restore
            and self.blocked_after_restore
            and self.position_before_cancel == 0
            and self.cancelled_status
            is ExecutionStatus.CANCELLED
            and self.final_position == 0
            and self.operator_reset_confirmed
            and not self.final_kill_switch_active
            and self.final_readiness
        )


def wait_until(
    condition: Callable[[], bool],
    *,
    description: str,
    timeout_seconds: float,
) -> None:
    """Wait for one asynchronous IB condition."""

    if not callable(condition):
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

    deadline = (
        time.monotonic()
        + float(timeout_seconds)
    )

    while not condition():
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for "
                f"{description.strip()}."
            )

        time.sleep(
            POLL_INTERVAL_SECONDS
        )


def build_trade_request(
    *,
    event_id: str,
) -> TradeRequest:
    """Build the one permitted working-order request."""

    if (
        not isinstance(event_id, str)
        or not event_id.strip()
    ):
        raise ValueError(
            "'event_id' must be a non-empty string."
        )

    return TradeRequest(
        event_id=event_id.strip(),
        signal_id="paper-working-order-signal-001",
        timestamp=datetime.now(
            timezone.utc
        ),
        environment=Environment.STAGING,
        intent=TradeIntent.BUY_TO_OPEN,
        symbol=SYMBOL,
        quantity=QUANTITY,
        stop_loss_points=STOP_LOSS_POINTS,
    )


def create_event_id() -> str:
    """Create a unique audit event ID."""

    return (
        "paper-working-order-"
        + datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
    )


def require_flat_account(
    broker_client: IBBrokerClient,
) -> int:
    """Require a completed broker snapshot that is flat."""

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
            "Working-order disconnect test requires "
            "the paper account to be completely flat."
        )

    return 0


def get_mbt_position(
    broker_client: IBBrokerClient,
) -> int:
    """Return signed MBT position from completed snapshot."""

    positions = (
        broker_client.get_raw_positions()
    )

    total = 0

    for position in positions:
        symbol = (
            position.symbol
            .strip()
            .upper()
        )

        if symbol in {
            SYMBOL,
            LOCAL_SYMBOL,
        }:
            total += position.quantity

    return total


def get_execution_record(
    ledger: ExecutionLedger,
    event_id: str,
) -> ExecutionRecord:
    """Require and return the current execution record."""

    record = ledger.get(
        event_id
    )

    if record is None:
        raise RuntimeError(
            "Execution record is missing."
        )

    return record


def wait_for_working_order(
    *,
    ledger: ExecutionLedger,
    event_id: str,
) -> ExecutionRecord:
    """Wait for ACKNOWLEDGED and reject any fill/terminal surprise."""

    def resolved() -> bool:
        record = ledger.get(
            event_id
        )

        if record is None:
            return False

        return record.status in {
            ExecutionStatus.ACKNOWLEDGED,
            ExecutionStatus.PARTIALLY_FILLED,
            ExecutionStatus.FILLED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.REJECTED,
        }

    wait_until(
        resolved,
        description="working limit-order acknowledgement",
        timeout_seconds=WORKING_TIMEOUT_SECONDS,
    )

    record = get_execution_record(
        ledger,
        event_id,
    )

    if (
        record.status
        is not ExecutionStatus.ACKNOWLEDGED
    ):
        raise RuntimeError(
            "Limit order did not remain safely working. "
            f"Observed BTS status: {record.status.value}."
        )

    return record


def require_blocked_during_uncertainty(
    readiness: IBTradingReadiness,
) -> bool:
    """Require kill-switch and execution-uncertainty blocking."""

    result = readiness.evaluate(
        positions_reconciled=True,
        execution_state_clear=False,
    )

    if result.ready:
        raise RuntimeError(
            "Safety violation: BTS remained ready "
            "during uncertain working-order state."
        )

    if (
        IBReadinessFailure.KILL_SWITCH_ACTIVE
        not in result.failures
    ):
        raise RuntimeError(
            "KillSwitchActive was not reported."
        )

    if (
        IBReadinessFailure.EXECUTION_UNCERTAINTY
        not in result.failures
    ):
        raise RuntimeError(
            "ExecutionUncertainty was not reported."
        )

    try:
        readiness.require_ready(
            positions_reconciled=True,
            execution_state_clear=False,
        )

    except RuntimeError:
        return True

    raise RuntimeError(
        "Safety violation: require_ready() "
        "allowed new trading."
    )


def confirm_reset(
    input_function: Callable[[str], str] = input,
) -> bool:
    """Require explicit operator confirmation."""

    response = input_function(
        "\nType RESET only after the working order is "
        "CANCELLED and the account is FLAT: "
    )

    return (
        isinstance(response, str)
        and response.strip().upper()
        == RESET_CONFIRMATION
    )


def run_working_order_disconnect_test(
    *,
    armed: bool,
    ledger_path: str | Path,
    input_function: Callable[[str], str] = input,
) -> IBWorkingOrderDisconnectResult:
    """Run the real working-order outage test."""

    if not isinstance(
        armed,
        bool,
    ):
        raise TypeError(
            "'armed' must be a bool."
        )

    if not armed:
        raise RuntimeError(
            "Working-order disconnect harness "
            "is not armed."
        )

    broker_client = IBBrokerClient()

    ledger = ExecutionLedger(
        ledger_path
    )

    if ledger.all_records():
        raise RuntimeError(
            "Working-order test ledger must be empty."
        )

    kill_switch = KillSwitch()

    controls = TradingControls(
        symbol=SYMBOL,
        quantity=QUANTITY,
        stop_loss_points=STOP_LOSS_POINTS,
    )

    controls.resume()

    daily_loss_guard = DailyLossGuard(
        MAX_DAILY_LOSS
    )

    risk_manager = RiskManager(
        controls,
        kill_switch,
        daily_loss_guard,
        allowed_symbols=(
            SYMBOL,
        ),
        max_order_quantity=1,
        max_absolute_position=1,
    )

    duplicate_guard = DuplicateOrderGuard()

    app = ObservingIBApiPositionApp(
        broker_client,
        execution_ledger=ledger,
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
        trading_controls=controls,
        kill_switch=kill_switch,
    )

    event_id = create_event_id()

    try:
        # -----------------------------------------------------
        # Healthy baseline.
        # -----------------------------------------------------

        manager.connect()

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="initial flat position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        initial_position_count = (
            require_flat_account(
                broker_client
            )
        )

        readiness.require_ready(
            positions_reconciled=True,
            execution_state_clear=True,
        )

        request = build_trade_request(
            event_id=event_id
        )

        risk = risk_manager.evaluate(
            request,
            current_position=0,
        )

        if not risk.approved:
            raise RuntimeError(
                "RiskManager rejected working-order test: "
                f"{risk.reason}"
            )

        factory = IBOrderFactory(
            exchange=EXCHANGE,
            currency="USD",
            trading_class=TRADING_CLASS,
            order_type="LMT",
            time_in_force="DAY",
            transmit=True,
            limit_price=LIMIT_PRICE,
        )

        execution_client = IBExecutionClient(
            order_factory=factory,
            duplicate_guard=duplicate_guard,
            execution_ledger=ledger,
            place_order_function=app.placeOrder,
        )

        broker_order_id = (
            app.order_id_allocator.allocate()
        )

        submission: IBSubmissionResult = (
            execution_client.submit(
                request,
                contract_month=CONTRACT_MONTH,
                broker_order_id=broker_order_id,
            )
        )

        order = submission.package.order

        if (
            order.action != "BUY"
            or order.totalQuantity != 1
            or order.orderType != "LMT"
            or Decimal(
                str(order.lmtPrice)
            ) != LIMIT_PRICE
            or order.transmit is not True
        ):
            raise RuntimeError(
                "Generated broker order violated "
                "working-order safety configuration."
            )

        working_record = (
            wait_for_working_order(
                ledger=ledger,
                event_id=event_id,
            )
        )

        # Fresh position check before deliberately breaking connectivity.
        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="pre-outage flat snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        require_flat_account(
            broker_client
        )

        print()
        print(
            "WORKING ORDER VERIFIED:"
        )
        print(
            f"BUY 1 MBT AUG26 LIMIT {LIMIT_PRICE}"
        )
        print(
            f"Broker order ID: {broker_order_id}"
        )
        print(
            "BTS status: ACKNOWLEDGED"
        )
        print(
            "Account position: FLAT"
        )
        print()
        print(
            "NOW INTERRUPT THIS COMPUTER'S INTERNET."
        )
        print(
            "Leave TWS OPEN and leave the API enabled."
        )
        print(
            "Do NOT cancel or modify the resting order."
        )
        print()
        print(
            "Waiting for real IB 1100..."
        )

        # -----------------------------------------------------
        # Real connectivity failure.
        # -----------------------------------------------------

        wait_until(
            lambda: (
                app.has_seen_error_code(
                    1100
                )
                and kill_switch.active
            ),
            description="real IB error 1100",
            timeout_seconds=LOSS_TIMEOUT_SECONDS,
        )

        blocked_after_loss = (
            require_blocked_during_uncertainty(
                readiness
            )
        )

        print()
        print(
            "IB 1100 RECEIVED."
        )
        print(
            "Kill switch ACTIVE."
        )
        print(
            "Working-order execution state is UNCERTAIN."
        )
        print(
            "New trading is BLOCKED."
        )
        print()
        print(
            "RESTORE INTERNET NOW."
        )
        print(
            "Do not touch the working order in TWS."
        )
        print()
        print(
            "Waiting for IB 1101 or 1102..."
        )

        # -----------------------------------------------------
        # Connectivity restored.
        # -----------------------------------------------------

        wait_until(
            app.has_seen_connection_restore,
            description="IB 1101 or 1102 restoration",
            timeout_seconds=RESTORE_TIMEOUT_SECONDS,
        )

        blocked_after_restore = (
            require_blocked_during_uncertainty(
                readiness
            )
        )

        if not kill_switch.active:
            raise RuntimeError(
                "Connectivity restoration improperly "
                "cleared the kill switch."
            )

        # -----------------------------------------------------
        # Reconcile position BEFORE cancellation.
        # -----------------------------------------------------

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="post-restore position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        position_before_cancel = (
            get_mbt_position(
                broker_client
            )
        )

        if position_before_cancel != 0:
            raise RuntimeError(
                "Working order filled during outage. "
                "BTS will NOT continue automatically. "
                f"Observed MBT position: "
                f"{position_before_cancel}."
            )

        current_record = (
            get_execution_record(
                ledger,
                event_id,
            )
        )

        if current_record.status in {
            ExecutionStatus.PARTIALLY_FILLED,
            ExecutionStatus.FILLED,
        }:
            raise RuntimeError(
                "Ledger reports a fill after connectivity "
                "restoration. Automatic cancellation/reset stopped."
            )

        # -----------------------------------------------------
        # Cancel the ORIGINAL order.
        # No new broker order ID is allocated.
        # -----------------------------------------------------

        print()
        print(
            "POSITION RECONCILIATION: FLAT."
        )
        print(
            "Cancelling ORIGINAL broker order ID "
            f"{broker_order_id}..."
        )

        app.cancelOrder(
            broker_order_id,
            OrderCancel(),
        )

        def cancellation_resolved() -> bool:
            record = ledger.get(
                event_id
            )

            if record is None:
                return False

            return record.status in {
                ExecutionStatus.CANCELLED,
                ExecutionStatus.PARTIALLY_FILLED,
                ExecutionStatus.FILLED,
                ExecutionStatus.REJECTED,
            }

        wait_until(
            cancellation_resolved,
            description="IB working-order cancellation",
            timeout_seconds=CANCEL_TIMEOUT_SECONDS,
        )

        cancelled_record = (
            get_execution_record(
                ledger,
                event_id,
            )
        )

        if (
            cancelled_record.status
            is not ExecutionStatus.CANCELLED
        ):
            raise RuntimeError(
                "Original working order did not reach "
                "CANCELLED. "
                f"Observed status: "
                f"{cancelled_record.status.value}."
            )

        # -----------------------------------------------------
        # Final broker reconciliation.
        # -----------------------------------------------------

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="final flat position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        final_position = (
            get_mbt_position(
                broker_client
            )
        )

        if final_position != 0:
            raise RuntimeError(
                "Account is not flat after cancellation. "
                f"Observed position: {final_position}."
            )

        operator_reset_confirmed = (
            confirm_reset(
                input_function
            )
        )

        if not operator_reset_confirmed:
            raise RuntimeError(
                "Operator did not authorize reset."
            )

        kill_switch.reset()

        final_readiness = (
            readiness.require_ready(
                positions_reconciled=True,
                execution_state_clear=True,
            )
        )

        return IBWorkingOrderDisconnectResult(
            event_id=event_id,
            broker_order_id=broker_order_id,
            initial_position_count=(
                initial_position_count
            ),
            working_status=(
                working_record.status
            ),
            saw_error_1100=(
                app.has_seen_error_code(
                    1100
                )
            ),
            kill_switch_after_loss=True,
            blocked_after_loss=(
                blocked_after_loss
            ),
            saw_restore=(
                app.has_seen_connection_restore()
            ),
            kill_switch_after_restore=True,
            blocked_after_restore=(
                blocked_after_restore
            ),
            position_before_cancel=(
                position_before_cancel
            ),
            cancelled_status=(
                cancelled_record.status
            ),
            final_position=(
                final_position
            ),
            operator_reset_confirmed=(
                operator_reset_confirmed
            ),
            final_kill_switch_active=(
                kill_switch.active
            ),
            final_readiness=(
                final_readiness.ready
            ),
        )

    finally:
        manager.disconnect()


def print_result(
    result: IBWorkingOrderDisconnectResult,
) -> None:
    """Print live working-order outage result."""

    if not isinstance(
        result,
        IBWorkingOrderDisconnectResult,
    ):
        raise TypeError(
            "'result' must be an "
            "IBWorkingOrderDisconnectResult."
        )

    print()
    print(
        "BTS / IB WORKING ORDER DISCONNECT TEST"
    )
    print(
        "========================================"
    )
    print(
        f"Event ID:                    {result.event_id}"
    )
    print(
        f"Broker order ID:             {result.broker_order_id}"
    )
    print(
        f"Working status:              {result.working_status.value}"
    )
    print(
        f"Saw IB 1100:                 {result.saw_error_1100}"
    )
    print(
        f"Kill switch after loss:      {result.kill_switch_after_loss}"
    )
    print(
        f"Blocked after loss:          {result.blocked_after_loss}"
    )
    print(
        f"Saw IB restore:              {result.saw_restore}"
    )
    print(
        f"Blocked after restore:       {result.blocked_after_restore}"
    )
    print(
        f"Position before cancel:      {result.position_before_cancel}"
    )
    print(
        f"Cancellation status:         {result.cancelled_status.value}"
    )
    print(
        f"Final MBT position:          {result.final_position}"
    )
    print(
        f"Operator reset:              {result.operator_reset_confirmed}"
    )
    print(
        f"Final kill switch active:    {result.final_kill_switch_active}"
    )
    print(
        f"Final readiness:             {result.final_readiness}"
    )
    print(
        "========================================"
    )

    if result.successful:
        print(
            "RESULT: PASS - working-order disconnect "
            "recovered safely without resubmission."
        )
    else:
        print(
            "RESULT: FAIL - working-order disconnect "
            "validation failed."
        )

    print()


def main(
    arguments: list[str] | None = None,
) -> int:
    """Run explicitly armed working-order outage test."""

    if arguments is None:
        arguments = sys.argv[1:]

    if arguments != [
        ARMING_ARGUMENT,
    ]:
        print()
        print(
            "WORKING ORDER NOT SENT."
        )
        print(
            "Required argument:"
        )
        print(
            f"    {ARMING_ARGUMENT}"
        )
        print()

        return 2

    ledger_path = (
        Path("data")
        / "ib_working_order_disconnect_test_2.db"
    )

    ledger_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        result = (
            run_working_order_disconnect_test(
                armed=True,
                ledger_path=ledger_path,
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
        print(
            "DO NOT RERUN AUTOMATICALLY."
        )
        print(
            "Inspect TWS, the broker position, "
            "and the durable ledger first."
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