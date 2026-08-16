"""Real IB paper test: keep an open MBT position after RESET.

Sequence:

    FLAT
        ↓
    BUY_TO_OPEN 1 MBT MKT
        ↓
    FILLED
        ↓
    broker position +1
        ↓
    internet interrupted
        ↓
    real IB 1100
        ↓
    kill switch ACTIVE
    new execution BLOCKED
        ↓
    internet restored
        ↓
    IB 1101 / 1102
        ↓
    fresh snapshot confirms +1
        ↓
    original BUY remains FILLED
        ↓
    operator types RESET
        ↓
    readiness restored
        ↓
    NO flatten order is sent
        ↓
    fresh snapshot still +1
        ↓
    attempt duplicate original BUY event
        ↓
    duplicate rejected
        ↓
    fresh snapshot still +1
        ↓
    PASS

The position is intentionally left open at the end of this test.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
import time

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
from app.ib_execution_client import IBExecutionClient
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
CURRENCY = "USD"
TRADING_CLASS = "MBT"
CONTRACT_MONTH = "20260828"

QUANTITY = 1
STOP_LOSS_POINTS = Decimal("500")
MAX_DAILY_LOSS = Decimal("1000")

CONNECTION_TIMEOUT_SECONDS = 10.0
EXECUTION_TIMEOUT_SECONDS = 30.0
LOSS_TIMEOUT_SECONDS = 600.0
RESTORE_TIMEOUT_SECONDS = 300.0
POLL_INTERVAL_SECONDS = 0.05

ARMING_ARGUMENT = "--confirm-keep-position-after-reset"
RESET_CONFIRMATION = "RESET"


@dataclass(frozen=True, slots=True)
class IBKeepPositionAfterResetResult:
    """Immutable result of keeping a position after recovery."""

    event_id: str
    broker_order_id: int

    entry_status: ExecutionStatus
    position_before_outage: int

    saw_error_1100: bool
    kill_switch_after_loss: bool
    execution_blocked_after_loss: bool

    saw_restore: bool
    kill_switch_after_restore: bool
    execution_blocked_after_restore: bool

    reconciled_position: int
    entry_status_after_restore: ExecutionStatus

    operator_reset_confirmed: bool
    readiness_after_reset: bool

    position_after_reset: int

    duplicate_rejected: bool
    position_after_duplicate_attempt: int

    final_kill_switch_active: bool

    @property
    def successful(self) -> bool:
        """Return True when RESET preserved the open position."""

        return (
            self.entry_status
            is ExecutionStatus.FILLED
            and self.position_before_outage == 1
            and self.saw_error_1100
            and self.kill_switch_after_loss
            and self.execution_blocked_after_loss
            and self.saw_restore
            and self.kill_switch_after_restore
            and self.execution_blocked_after_restore
            and self.reconciled_position == 1
            and self.entry_status_after_restore
            is ExecutionStatus.FILLED
            and self.operator_reset_confirmed
            and self.readiness_after_reset
            and self.position_after_reset == 1
            and self.duplicate_rejected
            and self.position_after_duplicate_attempt == 1
            and not self.final_kill_switch_active
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


def create_event_id() -> str:
    """Create a unique Eagle-style event ID."""

    return (
        "paper-keep-position-"
        + datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
    )


def build_entry_request(
    *,
    event_id: str,
) -> TradeRequest:
    """Build the one controlled BUY_TO_OPEN request."""

    if (
        not isinstance(event_id, str)
        or not event_id.strip()
    ):
        raise ValueError(
            "'event_id' must be a non-empty string."
        )

    return TradeRequest(
        event_id=event_id.strip(),
        signal_id="paper-keep-position-signal-001",
        timestamp=datetime.now(
            timezone.utc
        ),
        environment=Environment.STAGING,
        intent=TradeIntent.BUY_TO_OPEN,
        symbol=SYMBOL,
        quantity=QUANTITY,
        stop_loss_points=STOP_LOSS_POINTS,
    )


def get_mbt_position(
    broker_client: IBBrokerClient,
) -> int:
    """Return signed MBT position from completed snapshot."""

    if not isinstance(
        broker_client,
        IBBrokerClient,
    ):
        raise TypeError(
            "'broker_client' must be an IBBrokerClient."
        )

    total = 0

    for position in (
        broker_client.get_raw_positions()
    ):
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


def require_position(
    broker_client: IBBrokerClient,
    expected_position: int,
) -> int:
    """Require exact signed MBT broker position."""

    position = get_mbt_position(
        broker_client
    )

    if position != expected_position:
        raise RuntimeError(
            "Unexpected MBT broker position. "
            f"Expected {expected_position}, "
            f"observed {position}."
        )

    return position


def get_record(
    ledger: ExecutionLedger,
    event_id: str,
) -> ExecutionRecord:
    """Require and return durable execution record."""

    record = ledger.get(
        event_id
    )

    if record is None:
        raise RuntimeError(
            f"Execution record {event_id!r} is missing."
        )

    return record


def wait_for_fill(
    *,
    ledger: ExecutionLedger,
    event_id: str,
) -> ExecutionRecord:
    """Wait for entry execution to reach a terminal state."""

    def resolved() -> bool:
        record = ledger.get(
            event_id
        )

        if record is None:
            return False

        return record.status in {
            ExecutionStatus.FILLED,
            ExecutionStatus.CANCELLED,
            ExecutionStatus.REJECTED,
        }

    wait_until(
        resolved,
        description="paper entry fill",
        timeout_seconds=EXECUTION_TIMEOUT_SECONDS,
    )

    record = get_record(
        ledger,
        event_id,
    )

    if (
        record.status
        is not ExecutionStatus.FILLED
    ):
        raise RuntimeError(
            "Expected BUY_TO_OPEN to fill. "
            f"Observed status: {record.status.value}."
        )

    return record


def require_execution_blocked(
    readiness: IBTradingReadiness,
) -> bool:
    """Require kill switch to block new execution."""

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
            "KillSwitchActive was not reported."
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
        "allowed execution."
    )


def confirm_reset(
    input_function: Callable[[str], str] = input,
) -> bool:
    """Require explicit operator authorization."""

    if not callable(
        input_function
    ):
        raise TypeError(
            "'input_function' must be callable."
        )

    response = input_function(
        "\nBTS reconciled the original +1 MBT position. "
        "Type RESET to resume BTS while KEEPING "
        "the position open: "
    )

    return (
        isinstance(response, str)
        and response.strip().upper()
        == RESET_CONFIRMATION
    )


def attempt_duplicate_entry(
    *,
    execution_client: IBExecutionClient,
    trade_request: TradeRequest,
    contract_month: str,
    broker_order_id: int,
) -> bool:
    """Require the already-used Eagle event to be rejected.

    No broker call should occur because the durable execution
    ledger must reject the event before submission.
    """

    try:
        execution_client.submit(
            trade_request,
            contract_month=contract_month,
            broker_order_id=broker_order_id,
        )

    except ValueError:
        return True

    raise RuntimeError(
        "Safety violation: duplicate Eagle event "
        "was not rejected."
    )


def run_keep_position_after_reset_test(
    *,
    armed: bool,
    ledger_path: str | Path,
    input_function: Callable[[str], str] = input,
) -> IBKeepPositionAfterResetResult:
    """Run real RESET-with-position-preserved test."""

    if not isinstance(
        armed,
        bool,
    ):
        raise TypeError(
            "'armed' must be a bool."
        )

    if not armed:
        raise RuntimeError(
            "Keep-position harness is not armed."
        )

    broker_client = IBBrokerClient()

    ledger = ExecutionLedger(
        ledger_path
    )

    if ledger.all_records():
        raise RuntimeError(
            "Keep-position test ledger must be empty."
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

    entry_request = build_entry_request(
        event_id=event_id
    )

    try:
        # -----------------------------------------------------
        # Establish flat baseline.
        # -----------------------------------------------------

        manager.connect()

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="initial position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        require_position(
            broker_client,
            0,
        )

        readiness.require_ready(
            positions_reconciled=True,
            execution_state_clear=True,
        )

        # -----------------------------------------------------
        # Submit exactly one BUY_TO_OPEN.
        # -----------------------------------------------------

        risk = risk_manager.evaluate(
            entry_request,
            current_position=0,
        )

        if not risk.approved:
            raise RuntimeError(
                "RiskManager rejected entry: "
                f"{risk.reason}"
            )

        factory = IBOrderFactory(
            exchange=EXCHANGE,
            currency=CURRENCY,
            trading_class=TRADING_CLASS,
            order_type="MKT",
            time_in_force="DAY",
            transmit=True,
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

        execution_client.submit(
            entry_request,
            contract_month=CONTRACT_MONTH,
            broker_order_id=broker_order_id,
        )

        entry_record = wait_for_fill(
            ledger=ledger,
            event_id=event_id,
        )

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="post-entry position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        position_before_outage = (
            require_position(
                broker_client,
                1,
            )
        )

        print()
        print(
            "OPEN POSITION VERIFIED:"
        )
        print(
            "BUY_TO_OPEN 1 MBT AUG26 FILLED."
        )
        print(
            f"Broker order ID: {broker_order_id}"
        )
        print(
            "Broker position: +1 MBT"
        )
        print()
        print(
            "NOW INTERRUPT THIS COMPUTER'S INTERNET."
        )
        print(
            "Leave TWS OPEN and do not manually trade."
        )
        print()
        print(
            "Waiting for real IB 1100..."
        )

        # -----------------------------------------------------
        # Unexpected outage.
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
            require_execution_blocked(
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
            "Existing +1 MBT remains untouched."
        )
        print(
            "New trading is BLOCKED."
        )
        print()
        print(
            "RESTORE INTERNET NOW."
        )
        print(
            "Waiting for IB 1101 or 1102..."
        )

        # -----------------------------------------------------
        # Connectivity restoration.
        # -----------------------------------------------------

        wait_until(
            app.has_seen_connection_restore,
            description="IB connectivity restoration",
            timeout_seconds=RESTORE_TIMEOUT_SECONDS,
        )

        blocked_after_restore = (
            require_execution_blocked(
                readiness
            )
        )

        if not kill_switch.active:
            raise RuntimeError(
                "Connectivity restoration improperly "
                "cleared the kill switch."
            )

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="post-restore position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        reconciled_position = (
            require_position(
                broker_client,
                1,
            )
        )

        entry_after_restore = (
            get_record(
                ledger,
                event_id,
            )
        )

        if (
            entry_after_restore.status
            is not ExecutionStatus.FILLED
        ):
            raise RuntimeError(
                "Original entry no longer shows FILLED."
            )

        print()
        print(
            "RECOVERY RECONCILIATION COMPLETE:"
        )
        print(
            "Original BUY remains FILLED."
        )
        print(
            "Broker position remains exactly +1 MBT."
        )
        print(
            "Kill switch remains ACTIVE."
        )
        print(
            "NO flatten order has been sent."
        )

        operator_reset_confirmed = (
            confirm_reset(
                input_function
            )
        )

        if not operator_reset_confirmed:
            raise RuntimeError(
                "Operator did not authorize RESET."
            )

        # -----------------------------------------------------
        # RESET WITHOUT FLATTENING.
        # -----------------------------------------------------

        kill_switch.reset()

        readiness_after_reset = (
            readiness.require_ready(
                positions_reconciled=True,
                execution_state_clear=True,
            )
        )

        # Fresh snapshot proves RESET itself changed no position.
        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="post-reset position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        position_after_reset = (
            require_position(
                broker_client,
                1,
            )
        )

        print()
        print(
            "RESET COMPLETE."
        )
        print(
            "BTS is READY."
        )
        print(
            "Broker position is STILL +1 MBT."
        )
        print(
            "No flatten order was generated."
        )

        # -----------------------------------------------------
        # Duplicate Eagle-event test.
        #
        # Allocate a candidate ID only to prove the duplicate
        # event is rejected before placeOrder().
        # -----------------------------------------------------

        duplicate_candidate_order_id = (
            app.order_id_allocator.allocate()
        )

        duplicate_rejected = (
            attempt_duplicate_entry(
                execution_client=execution_client,
                trade_request=entry_request,
                contract_month=CONTRACT_MONTH,
                broker_order_id=(
                    duplicate_candidate_order_id
                ),
            )
        )

        # Verify no second broker exposure resulted.
        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="post-duplicate position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        position_after_duplicate_attempt = (
            require_position(
                broker_client,
                1,
            )
        )

        return IBKeepPositionAfterResetResult(
            event_id=event_id,
            broker_order_id=broker_order_id,

            entry_status=entry_record.status,
            position_before_outage=(
                position_before_outage
            ),

            saw_error_1100=(
                app.has_seen_error_code(
                    1100
                )
            ),
            kill_switch_after_loss=True,
            execution_blocked_after_loss=(
                blocked_after_loss
            ),

            saw_restore=(
                app.has_seen_connection_restore()
            ),
            kill_switch_after_restore=True,
            execution_blocked_after_restore=(
                blocked_after_restore
            ),

            reconciled_position=(
                reconciled_position
            ),
            entry_status_after_restore=(
                entry_after_restore.status
            ),

            operator_reset_confirmed=(
                operator_reset_confirmed
            ),
            readiness_after_reset=(
                readiness_after_reset.ready
            ),

            position_after_reset=(
                position_after_reset
            ),

            duplicate_rejected=(
                duplicate_rejected
            ),
            position_after_duplicate_attempt=(
                position_after_duplicate_attempt
            ),

            final_kill_switch_active=(
                kill_switch.active
            ),
        )

    finally:
        manager.disconnect()


def print_result(
    result: IBKeepPositionAfterResetResult,
) -> None:
    """Print the keep-position recovery result."""

    if not isinstance(
        result,
        IBKeepPositionAfterResetResult,
    ):
        raise TypeError(
            "'result' must be an "
            "IBKeepPositionAfterResetResult."
        )

    print()
    print(
        "BTS / IB KEEP POSITION AFTER RESET TEST"
    )
    print(
        "========================================"
    )
    print(
        f"Entry order ID:                 {result.broker_order_id}"
    )
    print(
        f"Entry status:                   {result.entry_status.value}"
    )
    print(
        f"Position before outage:         {result.position_before_outage}"
    )
    print(
        f"Saw real IB 1100:              {result.saw_error_1100}"
    )
    print(
        f"Kill switch after loss:        {result.kill_switch_after_loss}"
    )
    print(
        f"Saw IB restore:                {result.saw_restore}"
    )
    print(
        f"Kill switch after restore:     {result.kill_switch_after_restore}"
    )
    print(
        f"Reconciled position:           {result.reconciled_position}"
    )
    print(
        "Entry after restore:           "
        f"{result.entry_status_after_restore.value}"
    )
    print(
        f"Operator RESET confirmed:      {result.operator_reset_confirmed}"
    )
    print(
        f"Readiness after RESET:         {result.readiness_after_reset}"
    )
    print(
        f"Position after RESET:          {result.position_after_reset}"
    )
    print(
        f"Duplicate entry rejected:      {result.duplicate_rejected}"
    )
    print(
        "Position after duplicate try:  "
        f"{result.position_after_duplicate_attempt}"
    )
    print(
        f"Final kill switch active:      {result.final_kill_switch_active}"
    )
    print(
        "========================================"
    )

    if result.successful:
        print(
            "RESULT: PASS - RESET preserved the "
            "reconciled +1 MBT position."
        )
        print(
            "IMPORTANT: The paper account intentionally "
            "remains LONG +1 MBT."
        )

    else:
        print(
            "RESULT: FAIL - keep-position recovery "
            "validation failed."
        )

    print()


def main(
    arguments: list[str] | None = None,
) -> int:
    """Run explicitly armed keep-position recovery test."""

    if arguments is None:
        arguments = sys.argv[1:]

    if arguments != [
        ARMING_ARGUMENT,
    ]:
        print()
        print(
            "KEEP-POSITION TEST NOT STARTED."
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
        / "ib_keep_position_after_reset_test.db"
    )

    ledger_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        result = (
            run_keep_position_after_reset_test(
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
            "Inspect TWS position, broker orders, "
            "and durable ledger first."
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