"""Real IB paper disconnect test while an MBT position is open.

This harness deliberately creates exactly one +1 MBT paper
position, interrupts upstream IB connectivity, verifies the
position survives recovery unchanged, and then explicitly
closes that position after operator-confirmed recovery.

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
    execution BLOCKED
        ↓
    internet restored
        ↓
    IB 1101 / 1102
        ↓
    kill switch remains ACTIVE
        ↓
    fresh position snapshot = +1
        ↓
    original BUY remains FILLED
        ↓
    explicit operator RESET
        ↓
    readiness restored
        ↓
    SELL_TO_CLOSE 1 MBT MKT
        ↓
    FILLED
        ↓
    final position = 0
        ↓
    PASS

No entry order is ever resubmitted after the outage.
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

ARMING_ARGUMENT = "--confirm-open-position-disconnect"
RESET_CONFIRMATION = "RESET"


@dataclass(frozen=True, slots=True)
class IBOpenPositionDisconnectResult:
    """Immutable result of the open-position outage test."""

    entry_event_id: str
    entry_order_id: int
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

    close_event_id: str
    close_order_id: int
    close_status: ExecutionStatus
    final_position: int

    final_kill_switch_active: bool

    @property
    def successful(self) -> bool:
        """Return True when the entire outage lifecycle passed."""

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
            and self.close_status
            is ExecutionStatus.FILLED
            and self.final_position == 0
            and not self.final_kill_switch_active
            and self.close_order_id != self.entry_order_id
        )


def wait_until(
    condition: Callable[[], bool],
    *,
    description: str,
    timeout_seconds: float,
) -> None:
    """Wait for one asynchronous condition."""

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


def create_event_id(
    prefix: str,
) -> str:
    """Create a unique audit event identifier."""

    if (
        not isinstance(prefix, str)
        or not prefix.strip()
    ):
        raise ValueError(
            "'prefix' must be a non-empty string."
        )

    return (
        prefix.strip()
        + "-"
        + datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
    )


def build_trade_request(
    *,
    event_id: str,
    signal_id: str,
    intent: TradeIntent,
) -> TradeRequest:
    """Build one controlled MBT paper TradeRequest."""

    if (
        not isinstance(event_id, str)
        or not event_id.strip()
    ):
        raise ValueError(
            "'event_id' must be a non-empty string."
        )

    if (
        not isinstance(signal_id, str)
        or not signal_id.strip()
    ):
        raise ValueError(
            "'signal_id' must be a non-empty string."
        )

    if not isinstance(
        intent,
        TradeIntent,
    ):
        raise TypeError(
            "'intent' must be a TradeIntent."
        )

    if intent not in {
        TradeIntent.BUY_TO_OPEN,
        TradeIntent.SELL_TO_CLOSE,
    }:
        raise ValueError(
            "Open-position disconnect harness permits only "
            "BUY_TO_OPEN or SELL_TO_CLOSE."
        )

    return TradeRequest(
        event_id=event_id.strip(),
        signal_id=signal_id.strip(),
        timestamp=datetime.now(
            timezone.utc
        ),
        environment=Environment.STAGING,
        intent=intent,
        symbol=SYMBOL,
        quantity=QUANTITY,
        stop_loss_points=STOP_LOSS_POINTS,
    )


def get_mbt_position(
    broker_client: IBBrokerClient,
) -> int:
    """Return the signed MBT position from a completed snapshot."""

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
    """Require an exact signed MBT broker position."""

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
    """Require and return one durable execution record."""

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
    """Wait until an execution fills or reaches another terminal state."""

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
        description=f"execution {event_id} resolution",
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
            "Expected paper order to fill. "
            f"Observed status: {record.status.value}."
        )

    return record


def require_execution_blocked(
    readiness: IBTradingReadiness,
) -> bool:
    """Require the outage kill switch to block new execution."""

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
            "during connectivity emergency."
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
        "Safety violation: require_ready() allowed execution."
    )


def confirm_reset(
    input_function: Callable[[str], str] = input,
) -> bool:
    """Require explicit human confirmation before recovery."""

    if not callable(
        input_function
    ):
        raise TypeError(
            "'input_function' must be callable."
        )

    response = input_function(
        "\nBTS has reconciled the original +1 MBT position. "
        "Type RESET to clear the kill switch and permit "
        "the controlled SELL_TO_CLOSE: "
    )

    return (
        isinstance(response, str)
        and response.strip().upper()
        == RESET_CONFIRMATION
    )


def run_open_position_disconnect_test(
    *,
    armed: bool,
    ledger_path: str | Path,
    input_function: Callable[[str], str] = input,
) -> IBOpenPositionDisconnectResult:
    """Run the real open-position connectivity-loss test."""

    if not isinstance(
        armed,
        bool,
    ):
        raise TypeError(
            "'armed' must be a bool."
        )

    if not armed:
        raise RuntimeError(
            "Open-position disconnect harness is not armed."
        )

    broker_client = IBBrokerClient()

    ledger = ExecutionLedger(
        ledger_path
    )

    if ledger.all_records():
        raise RuntimeError(
            "Open-position test ledger must be empty."
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

    entry_event_id = create_event_id(
        "paper-open-position-entry"
    )

    close_event_id = create_event_id(
        "paper-open-position-close"
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
        # BUY_TO_OPEN exactly 1 MBT.
        # -----------------------------------------------------

        entry_request = build_trade_request(
            event_id=entry_event_id,
            signal_id="paper-open-position-signal-001",
            intent=TradeIntent.BUY_TO_OPEN,
        )

        entry_risk = risk_manager.evaluate(
            entry_request,
            current_position=0,
        )

        if not entry_risk.approved:
            raise RuntimeError(
                "RiskManager rejected controlled entry: "
                f"{entry_risk.reason}"
            )

        market_factory = IBOrderFactory(
            exchange=EXCHANGE,
            currency=CURRENCY,
            trading_class=TRADING_CLASS,
            order_type="MKT",
            time_in_force="DAY",
            transmit=True,
        )

        execution_client = IBExecutionClient(
            order_factory=market_factory,
            duplicate_guard=duplicate_guard,
            execution_ledger=ledger,
            place_order_function=app.placeOrder,
        )

        entry_order_id = (
            app.order_id_allocator.allocate()
        )

        execution_client.submit(
            entry_request,
            contract_month=CONTRACT_MONTH,
            broker_order_id=entry_order_id,
        )

        entry_record = wait_for_fill(
            ledger=ledger,
            event_id=entry_event_id,
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
            f"Broker entry order ID: {entry_order_id}"
        )
        print(
            "Broker position: +1 MBT"
        )
        print()
        print(
            "NOW INTERRUPT THIS COMPUTER'S INTERNET."
        )
        print(
            "Leave TWS OPEN and leave the API enabled."
        )
        print(
            "Do NOT manually close or modify the position."
        )
        print()
        print(
            "Waiting for real IB 1100..."
        )

        # -----------------------------------------------------
        # Unexpected connectivity loss.
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
            "New execution BLOCKED."
        )
        print(
            "Existing +1 MBT position is intentionally "
            "left untouched."
        )
        print()
        print(
            "RESTORE INTERNET NOW."
        )
        print(
            "Leave TWS open and do not manually trade."
        )
        print()
        print(
            "Waiting for IB 1101 or 1102..."
        )

        # -----------------------------------------------------
        # Connectivity recovery.
        # -----------------------------------------------------

        wait_until(
            app.has_seen_connection_restore,
            description="IB 1101 or 1102 restoration",
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

        # -----------------------------------------------------
        # Reconcile the EXISTING broker position.
        # -----------------------------------------------------

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

        entry_record_after_restore = (
            get_record(
                ledger,
                entry_event_id,
            )
        )

        if (
            entry_record_after_restore.status
            is not ExecutionStatus.FILLED
        ):
            raise RuntimeError(
                "Original entry execution changed unexpectedly. "
                f"Observed status: "
                f"{entry_record_after_restore.status.value}."
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
            "No duplicate entry has been submitted."
        )
        print(
            "Kill switch remains ACTIVE."
        )

        operator_reset_confirmed = (
            confirm_reset(
                input_function
            )
        )

        if not operator_reset_confirmed:
            raise RuntimeError(
                "Operator did not authorize recovery."
            )

        # -----------------------------------------------------
        # Explicit recovery authorization.
        # -----------------------------------------------------

        kill_switch.reset()

        readiness_after_reset = (
            readiness.require_ready(
                positions_reconciled=True,
                execution_state_clear=True,
            )
        )

        # -----------------------------------------------------
        # Controlled SELL_TO_CLOSE.
        # -----------------------------------------------------

        close_request = build_trade_request(
            event_id=close_event_id,
            signal_id="paper-open-position-signal-001",
            intent=TradeIntent.SELL_TO_CLOSE,
        )

        close_risk = risk_manager.evaluate(
            close_request,
            current_position=1,
        )

        if not close_risk.approved:
            raise RuntimeError(
                "RiskManager rejected controlled close: "
                f"{close_risk.reason}"
            )

        if close_risk.projected_position != 0:
            raise RuntimeError(
                "SELL_TO_CLOSE did not project a flat position."
            )

        close_order_id = (
            app.order_id_allocator.allocate()
        )

        if close_order_id == entry_order_id:
            raise RuntimeError(
                "Safety violation: entry broker order ID "
                "was reused for the close."
            )

        execution_client.submit(
            close_request,
            contract_month=CONTRACT_MONTH,
            broker_order_id=close_order_id,
        )

        close_record = wait_for_fill(
            ledger=ledger,
            event_id=close_event_id,
        )

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="final flat position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        final_position = (
            require_position(
                broker_client,
                0,
            )
        )

        return IBOpenPositionDisconnectResult(
            entry_event_id=entry_event_id,
            entry_order_id=entry_order_id,
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
                entry_record_after_restore.status
            ),
            operator_reset_confirmed=(
                operator_reset_confirmed
            ),
            readiness_after_reset=(
                readiness_after_reset.ready
            ),
            close_event_id=close_event_id,
            close_order_id=close_order_id,
            close_status=close_record.status,
            final_position=final_position,
            final_kill_switch_active=(
                kill_switch.active
            ),
        )

    finally:
        manager.disconnect()


def print_result(
    result: IBOpenPositionDisconnectResult,
) -> None:
    """Print the open-position outage result."""

    if not isinstance(
        result,
        IBOpenPositionDisconnectResult,
    ):
        raise TypeError(
            "'result' must be an "
            "IBOpenPositionDisconnectResult."
        )

    print()
    print(
        "BTS / IB OPEN POSITION DISCONNECT TEST"
    )
    print(
        "========================================"
    )
    print(
        f"Entry order ID:                {result.entry_order_id}"
    )
    print(
        f"Entry status:                  {result.entry_status.value}"
    )
    print(
        f"Position before outage:        {result.position_before_outage}"
    )
    print(
        f"Saw real IB 1100:             {result.saw_error_1100}"
    )
    print(
        f"Kill switch after loss:       {result.kill_switch_after_loss}"
    )
    print(
        f"Execution blocked after loss: {result.execution_blocked_after_loss}"
    )
    print(
        f"Saw IB restore:               {result.saw_restore}"
    )
    print(
        f"Kill switch after restore:    {result.kill_switch_after_restore}"
    )
    print(
        f"Reconciled position:          {result.reconciled_position}"
    )
    print(
        "Entry status after restore:   "
        f"{result.entry_status_after_restore.value}"
    )
    print(
        f"Operator reset:               {result.operator_reset_confirmed}"
    )
    print(
        f"Readiness after reset:        {result.readiness_after_reset}"
    )
    print(
        f"Close order ID:               {result.close_order_id}"
    )
    print(
        f"Close status:                 {result.close_status.value}"
    )
    print(
        f"Final MBT position:           {result.final_position}"
    )
    print(
        f"Final kill switch active:     {result.final_kill_switch_active}"
    )
    print(
        "========================================"
    )

    if result.successful:
        print(
            "RESULT: PASS - open-position disconnect "
            "recovered without duplicate exposure."
        )
    else:
        print(
            "RESULT: FAIL - open-position disconnect "
            "validation failed."
        )

    print()


def main(
    arguments: list[str] | None = None,
) -> int:
    """Run explicitly armed open-position outage test."""

    if arguments is None:
        arguments = sys.argv[1:]

    if arguments != [
        ARMING_ARGUMENT,
    ]:
        print()
        print(
            "OPEN-POSITION TEST NOT STARTED."
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
        / "ib_open_position_disconnect_test.db"
    )

    ledger_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        result = (
            run_open_position_disconnect_test(
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
            "Inspect the TWS position, broker orders, "
            "and execution ledger first."
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