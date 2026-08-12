"""Controlled first Interactive Brokers paper-order test for BTS.

This harness is intentionally restricted to exactly one:

    BUY_TO_OPEN
    1 MBT
    August 2026 Micro Bitcoin futures
    TWS paper account
    CME
    Market order

The harness refuses to transmit unless explicitly armed from the
command line.

Before the first real run:

    1. Complete all offline tests.
    2. Confirm the paper account is flat.
    3. Confirm TWS is connected to the DU simulated account.
    4. Only then disable TWS Read-Only API.
    5. Run with --confirm-paper-order.

This script leaves the successfully filled 1-MBT paper position
OPEN so BTS can verify the broker position before we separately
test SELL_TO_CLOSE.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
import time
from collections.abc import Callable

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
from app.ib_api_position_app import IBApiPositionApp
from app.ib_broker_client import IBBrokerClient
from app.ib_connection_manager import IBConnectionManager
from app.ib_execution_client import (
    IBExecutionClient,
    IBSubmissionResult,
)
from app.ib_order_factory import IBOrderFactory
from app.ib_trading_readiness import IBTradingReadiness
from app.kill_switch import KillSwitch
from app.risk_manager import (
    RiskDecision,
    RiskManager,
)
from app.trading_controls import TradingControls


HOST = "127.0.0.1"
PORT = 7497
CLIENT_ID = 1

SYMBOL = "MBT"
EXPECTED_LOCAL_SYMBOL = "MBTQ6"
EXCHANGE = "CME"
CURRENCY = "USD"
TRADING_CLASS = "MBT"
CONTRACT_MONTH = "20260828"

PAPER_QUANTITY = 1
STOP_LOSS_POINTS = Decimal("500")
MAX_DAILY_LOSS = Decimal("1000")

CONNECTION_TIMEOUT_SECONDS = 10.0
EXECUTION_TIMEOUT_SECONDS = 20.0

ARMING_ARGUMENT = "--confirm-paper-order"


@dataclass(frozen=True, slots=True)
class IBPaperOrderResult:
    """Immutable result of the first BTS paper-order test."""

    event_id: str
    broker_order_id: int
    initial_position_count: int
    initial_position: int
    risk_approved: bool
    readiness_passed: bool
    submitted_status: ExecutionStatus
    final_status: ExecutionStatus
    final_position: int
    kill_switch_active: bool

    @property
    def successful(self) -> bool:
        """Return True when the complete paper test succeeded."""

        return (
            self.initial_position_count == 0
            and self.initial_position == 0
            and self.risk_approved
            and self.readiness_passed
            and self.submitted_status
            is ExecutionStatus.SUBMITTED
            and self.final_status
            is ExecutionStatus.FILLED
            and self.final_position == 1
            and not self.kill_switch_active
        )


def build_trade_request(
    *,
    event_id: str,
) -> TradeRequest:
    """Build the only TradeRequest permitted by this harness."""

    if (
        not isinstance(event_id, str)
        or not event_id.strip()
    ):
        raise ValueError(
            "'event_id' must be a non-empty string."
        )

    trade_request = TradeRequest(
        event_id=event_id.strip(),
        signal_id="paper-test-signal-001",
        timestamp=datetime.now(
            timezone.utc
        ),
        environment=Environment.STAGING,
        intent=TradeIntent.BUY_TO_OPEN,
        symbol=SYMBOL,
        quantity=PAPER_QUANTITY,
        stop_loss_points=STOP_LOSS_POINTS,
    )

    validate_trade_request(
        trade_request
    )

    return trade_request


def validate_trade_request(
    trade_request: TradeRequest,
) -> None:
    """Enforce the hard-coded first-paper-order safety policy."""

    if not isinstance(
        trade_request,
        TradeRequest,
    ):
        raise TypeError(
            "'trade_request' must be a TradeRequest."
        )

    if (
        trade_request.environment
        is not Environment.STAGING
    ):
        raise RuntimeError(
            "Paper-order harness requires "
            "Environment.STAGING."
        )

    if trade_request.symbol != SYMBOL:
        raise RuntimeError(
            "Paper-order harness permits only MBT."
        )

    if trade_request.quantity != 1:
        raise RuntimeError(
            "Paper-order harness permits exactly "
            "1 MBT contract."
        )

    if (
        trade_request.intent
        is not TradeIntent.BUY_TO_OPEN
    ):
        raise RuntimeError(
            "First paper-order harness permits only "
            "BUY_TO_OPEN."
        )


def get_mbt_position(
    broker_client: IBBrokerClient,
) -> int:
    """Return the current signed MBT position from broker state."""

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
            EXPECTED_LOCAL_SYMBOL,
        }:
            total += position.quantity

    return total


def require_completely_flat(
    broker_client: IBBrokerClient,
) -> None:
    """Require the entire paper account position snapshot to be flat."""

    positions = (
        broker_client.get_raw_positions()
    )

    if positions:
        raise RuntimeError(
            "First paper-order test requires "
            "a completely flat paper account."
        )

    if get_mbt_position(
        broker_client
    ) != 0:
        raise RuntimeError(
            "First paper-order test requires "
            "MBT position to be zero."
        )


def wait_until(
    condition: Callable[[], bool],
    *,
    description: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.05,
) -> None:
    """Wait for an asynchronous IB condition."""

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

    deadline = (
        time.monotonic()
        + float(
            timeout_seconds
        )
    )

    while not condition():
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for "
                f"{description.strip()}."
            )

        time.sleep(
            poll_interval_seconds
        )


def wait_for_execution_resolution(
    *,
    execution_ledger: ExecutionLedger,
    event_id: str,
    kill_switch: KillSwitch,
    timeout_seconds: float,
) -> ExecutionRecord:
    """Wait for FILLED, CANCELLED, REJECTED, or emergency state."""

    def resolved() -> bool:
        if kill_switch.active:
            return True

        record = execution_ledger.get(
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
        description="IB paper-order resolution",
        timeout_seconds=timeout_seconds,
    )

    if kill_switch.active:
        raise RuntimeError(
            "BTS kill switch activated while "
            "waiting for the paper order: "
            f"{kill_switch.reason}"
        )

    record = execution_ledger.get(
        event_id
    )

    if record is None:
        raise RuntimeError(
            "Paper execution disappeared from "
            "the durable ledger."
        )

    return record


def create_event_id() -> str:
    """Create a unique audit-friendly paper event ID."""

    now = datetime.now(
        timezone.utc
    )

    return (
        "paper-order-"
        + now.strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
    )


def run_paper_order_test(
    *,
    armed: bool,
    ledger_path: str | Path,
) -> IBPaperOrderResult:
    """Submit exactly one controlled 1-MBT paper order."""

    if not isinstance(
        armed,
        bool,
    ):
        raise TypeError(
            "'armed' must be a bool."
        )

    if not armed:
        raise RuntimeError(
            "Paper-order harness is not armed. "
            f"Run with {ARMING_ARGUMENT} only after "
            "all offline tests pass."
        )

    broker_client = IBBrokerClient()

    execution_ledger = ExecutionLedger(
        ledger_path
    )

    if execution_ledger.all_records():
        raise RuntimeError(
            "Paper-order ledger must be empty "
            "before the first submission."
        )

    kill_switch = KillSwitch()

    trading_controls = TradingControls(
        symbol=SYMBOL,
        quantity=1,
        stop_loss_points=STOP_LOSS_POINTS,
    )

    daily_loss_guard = DailyLossGuard(
        MAX_DAILY_LOSS
    )

    risk_manager = RiskManager(
        trading_controls,
        kill_switch,
        daily_loss_guard,
        allowed_symbols=(
            SYMBOL,
        ),
        max_order_quantity=1,
        max_absolute_position=1,
    )

    duplicate_guard = DuplicateOrderGuard()

    app = IBApiPositionApp(
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

    event_id = create_event_id()

    try:
        manager.connect()

        wait_until(
            lambda: app.api_ready.ready,
            description="IB nextValidId handshake",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        if kill_switch.active:
            raise RuntimeError(
                "Kill switch activated during "
                "IB handshake: "
                f"{kill_switch.reason}"
            )

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="initial IB position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        initial_positions = (
            broker_client.get_raw_positions()
        )

        initial_position_count = len(
            initial_positions
        )

        initial_position = (
            get_mbt_position(
                broker_client
            )
        )

        require_completely_flat(
            broker_client
        )

        trading_controls.resume()

        readiness = IBTradingReadiness(
            api_ready=app.api_ready,
            order_id_allocator=(
                app.order_id_allocator
            ),
            broker_client=broker_client,
            trading_controls=trading_controls,
            kill_switch=kill_switch,
        )

        readiness_result = (
            readiness.require_ready(
                positions_reconciled=True,
                execution_state_clear=True,
            )
        )

        trade_request = (
            build_trade_request(
                event_id=event_id
            )
        )

        risk_decision: RiskDecision = (
            risk_manager.evaluate(
                trade_request,
                current_position=(
                    initial_position
                ),
            )
        )

        if not risk_decision.approved:
            raise RuntimeError(
                "RiskManager rejected first "
                "paper order: "
                f"{risk_decision.reason}"
            )

        order_factory = IBOrderFactory(
            exchange=EXCHANGE,
            currency=CURRENCY,
            trading_class=TRADING_CLASS,
            order_type="MKT",
            time_in_force="DAY",
            transmit=True,
        )

        execution_client = (
            IBExecutionClient(
                order_factory=order_factory,
                duplicate_guard=duplicate_guard,
                execution_ledger=(
                    execution_ledger
                ),
                place_order_function=(
                    app.placeOrder
                ),
            )
        )

        broker_order_id = (
            app.order_id_allocator.allocate()
        )

        submission: IBSubmissionResult = (
            execution_client.submit(
                trade_request,
                contract_month=(
                    CONTRACT_MONTH
                ),
                broker_order_id=(
                    broker_order_id
                ),
            )
        )

        if (
            submission.package.order.totalQuantity
            != 1
        ):
            raise RuntimeError(
                "Safety violation: generated paper "
                "order quantity is not exactly 1."
            )

        if (
            submission.package.order.action
            != "BUY"
        ):
            raise RuntimeError(
                "Safety violation: first paper "
                "order action is not BUY."
            )

        if (
            submission.package.order.transmit
            is not True
        ):
            raise RuntimeError(
                "Paper order was not configured "
                "for transmission."
            )

        submitted_status = (
            submission.ledger_record.status
        )

        final_record = (
            wait_for_execution_resolution(
                execution_ledger=(
                    execution_ledger
                ),
                event_id=event_id,
                kill_switch=kill_switch,
                timeout_seconds=(
                    EXECUTION_TIMEOUT_SECONDS
                ),
            )
        )

        if (
            final_record.status
            is not ExecutionStatus.FILLED
        ):
            raise RuntimeError(
                "Paper order did not fill. "
                f"Final BTS state: "
                f"{final_record.status.value}. "
                f"Reason: {final_record.reason}"
            )

        # Obtain a fresh broker position snapshot after the fill.
        if app.position_request_active:
            app.cancel_position_updates()

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="post-fill IB position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        final_position = (
            get_mbt_position(
                broker_client
            )
        )

        if final_position != 1:
            raise RuntimeError(
                "Filled paper order did not produce "
                "the expected +1 MBT broker position. "
                f"Observed position: {final_position}."
            )

        return IBPaperOrderResult(
            event_id=event_id,
            broker_order_id=broker_order_id,
            initial_position_count=(
                initial_position_count
            ),
            initial_position=(
                initial_position
            ),
            risk_approved=(
                risk_decision.approved
            ),
            readiness_passed=(
                readiness_result.ready
            ),
            submitted_status=(
                submitted_status
            ),
            final_status=(
                final_record.status
            ),
            final_position=(
                final_position
            ),
            kill_switch_active=(
                kill_switch.active
            ),
        )

    finally:
        if (
            app.position_request_active
            and app.isConnected()
        ):
            app.cancel_position_updates()

        manager.disconnect()


def print_result(
    result: IBPaperOrderResult,
) -> None:
    """Print a human-readable first-paper-order result."""

    if not isinstance(
        result,
        IBPaperOrderResult,
    ):
        raise TypeError(
            "'result' must be an IBPaperOrderResult."
        )

    print()
    print(
        "BTS / IB FIRST PAPER ORDER TEST"
    )
    print(
        "========================================"
    )
    print(
        f"Event ID:                 {result.event_id}"
    )
    print(
        f"Broker order ID:          {result.broker_order_id}"
    )
    print(
        "Initial position count:   "
        f"{result.initial_position_count}"
    )
    print(
        f"Initial MBT position:     {result.initial_position}"
    )
    print(
        f"Risk approved:            {result.risk_approved}"
    )
    print(
        "Readiness passed:         "
        f"{result.readiness_passed}"
    )
    print(
        "Ledger after submission:  "
        f"{result.submitted_status.value}"
    )
    print(
        "Final execution status:   "
        f"{result.final_status.value}"
    )
    print(
        f"Final MBT position:       {result.final_position}"
    )
    print(
        "Kill switch active:       "
        f"{result.kill_switch_active}"
    )
    print(
        "========================================"
    )

    if result.successful:
        print(
            "RESULT: PASS - 1 MBT paper BUY_TO_OPEN "
            "completed successfully."
        )

        print(
            "IMPORTANT: The +1 MBT paper position "
            "remains OPEN."
        )

    else:
        print(
            "RESULT: FAIL - paper-order validation failed."
        )

    print()


def main(
    arguments: list[str] | None = None,
) -> int:
    """Run the explicitly armed first IB paper-order test."""

    if arguments is None:
        arguments = sys.argv[1:]

    armed = (
        arguments
        == [
            ARMING_ARGUMENT,
        ]
    )

    if not armed:
        print()
        print(
            "PAPER ORDER NOT SENT."
        )
        print(
            "This harness requires the exact argument:"
        )
        print(
            f"    {ARMING_ARGUMENT}"
        )
        print()

        return 2

    ledger_path = (
        Path("data")
        / "ib_first_paper_order.db"
    )

    ledger_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        result = (
            run_paper_order_test(
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

        return 1

    print_result(
        result
    )

    if result.successful:
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )