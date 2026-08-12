"""Controlled Interactive Brokers paper SELL_TO_CLOSE test for BTS.

This harness is intentionally restricted to exactly one:

    SELL_TO_CLOSE
    1 MBT
    August 2026 Micro Bitcoin futures
    TWS paper account
    CME
    Market order

It requires the paper account to begin with exactly +1 MBT and
expects the broker position to become flat after the fill.

The harness refuses to transmit unless explicitly armed using
--confirm-paper-close.
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
EXPECTED_INITIAL_POSITION = 1
EXPECTED_FINAL_POSITION = 0

STOP_LOSS_POINTS = Decimal("500")
MAX_DAILY_LOSS = Decimal("1000")

CONNECTION_TIMEOUT_SECONDS = 10.0
EXECUTION_TIMEOUT_SECONDS = 20.0

ARMING_ARGUMENT = "--confirm-paper-close"


@dataclass(frozen=True, slots=True)
class IBPaperCloseResult:
    """Immutable result of the first BTS paper close test."""

    event_id: str
    broker_order_id: int
    initial_position: int
    risk_approved: bool
    projected_position: int
    readiness_passed: bool
    submitted_status: ExecutionStatus
    final_status: ExecutionStatus
    final_position: int
    kill_switch_active: bool

    @property
    def successful(self) -> bool:
        """Return True when the paper close completed correctly."""

        return (
            self.initial_position == EXPECTED_INITIAL_POSITION
            and self.risk_approved
            and self.projected_position == EXPECTED_FINAL_POSITION
            and self.readiness_passed
            and self.submitted_status
            is ExecutionStatus.SUBMITTED
            and self.final_status
            is ExecutionStatus.FILLED
            and self.final_position == EXPECTED_FINAL_POSITION
            and not self.kill_switch_active
        )


def build_close_trade_request(
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
        signal_id="paper-close-signal-001",
        timestamp=datetime.now(
            timezone.utc
        ),
        environment=Environment.STAGING,
        intent=TradeIntent.SELL_TO_CLOSE,
        symbol=SYMBOL,
        quantity=PAPER_QUANTITY,
        stop_loss_points=STOP_LOSS_POINTS,
    )

    validate_close_trade_request(
        trade_request
    )

    return trade_request


def validate_close_trade_request(
    trade_request: TradeRequest,
) -> None:
    """Enforce the hard-coded first-paper-close safety policy."""

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
            "Paper-close harness requires "
            "Environment.STAGING."
        )

    if trade_request.symbol != SYMBOL:
        raise RuntimeError(
            "Paper-close harness permits only MBT."
        )

    if trade_request.quantity != PAPER_QUANTITY:
        raise RuntimeError(
            "Paper-close harness permits exactly "
            "1 MBT contract."
        )

    if (
        trade_request.intent
        is not TradeIntent.SELL_TO_CLOSE
    ):
        raise RuntimeError(
            "Paper-close harness permits only "
            "SELL_TO_CLOSE."
        )


def get_mbt_position(
    broker_client: IBBrokerClient,
) -> int:
    """Return the current signed MBT broker position."""

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


def require_exact_long_position(
    broker_client: IBBrokerClient,
) -> int:
    """Require exactly +1 MBT before SELL_TO_CLOSE."""

    positions = (
        broker_client.get_raw_positions()
    )

    position = get_mbt_position(
        broker_client
    )

    if position != EXPECTED_INITIAL_POSITION:
        raise RuntimeError(
            "Paper-close test requires exactly "
            "+1 MBT before submission. "
            f"Observed MBT position: {position}."
        )

    matching_positions = tuple(
        broker_position
        for broker_position in positions
        if (
            broker_position.symbol
            .strip()
            .upper()
            in {
                SYMBOL,
                EXPECTED_LOCAL_SYMBOL,
            }
        )
    )

    if len(matching_positions) != 1:
        raise RuntimeError(
            "Paper-close test requires exactly one "
            "MBT broker position record."
        )

    return position


def wait_until(
    condition: Callable[[], bool],
    *,
    description: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.05,
) -> None:
    """Wait for one asynchronous IB condition."""

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
    """Wait for the close execution to reach a terminal state."""

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
        description="IB paper-close resolution",
        timeout_seconds=timeout_seconds,
    )

    if kill_switch.active:
        raise RuntimeError(
            "BTS kill switch activated while "
            "waiting for paper close: "
            f"{kill_switch.reason}"
        )

    record = execution_ledger.get(
        event_id
    )

    if record is None:
        raise RuntimeError(
            "Paper-close execution disappeared "
            "from the durable ledger."
        )

    return record


def create_event_id() -> str:
    """Create a unique paper-close event identifier."""

    now = datetime.now(
        timezone.utc
    )

    return (
        "paper-close-"
        + now.strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
    )


def run_paper_close_test(
    *,
    armed: bool,
    ledger_path: str | Path,
) -> IBPaperCloseResult:
    """Submit exactly one controlled SELL_TO_CLOSE paper order."""

    if not isinstance(
        armed,
        bool,
    ):
        raise TypeError(
            "'armed' must be a bool."
        )

    if not armed:
        raise RuntimeError(
            "Paper-close harness is not armed. "
            f"Run with {ARMING_ARGUMENT} only after "
            "offline tests pass."
        )

    broker_client = IBBrokerClient()

    execution_ledger = ExecutionLedger(
        ledger_path
    )

    if execution_ledger.all_records():
        raise RuntimeError(
            "Paper-close ledger must be empty "
            "before submission."
        )

    kill_switch = KillSwitch()

    trading_controls = TradingControls(
        symbol=SYMBOL,
        quantity=PAPER_QUANTITY,
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

        initial_position = (
            require_exact_long_position(
                broker_client
            )
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
            build_close_trade_request(
                event_id=event_id
            )
        )

        risk_decision: RiskDecision = (
            risk_manager.evaluate(
                trade_request,
                current_position=initial_position,
            )
        )

        if not risk_decision.approved:
            raise RuntimeError(
                "RiskManager rejected paper close: "
                f"{risk_decision.reason}"
            )

        if (
            risk_decision.projected_position
            != EXPECTED_FINAL_POSITION
        ):
            raise RuntimeError(
                "RiskManager close projection is not flat. "
                f"Projected position: "
                f"{risk_decision.projected_position}."
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
            != PAPER_QUANTITY
        ):
            raise RuntimeError(
                "Safety violation: close quantity "
                "is not exactly 1."
            )

        if (
            submission.package.order.action
            != "SELL"
        ):
            raise RuntimeError(
                "Safety violation: SELL_TO_CLOSE "
                "did not produce IB SELL action."
            )

        if (
            submission.package.order.transmit
            is not True
        ):
            raise RuntimeError(
                "Paper close was not configured "
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
                "Paper close did not fill. "
                f"Final BTS state: "
                f"{final_record.status.value}. "
                f"Reason: {final_record.reason}"
            )

        # positionEnd() now cancels the previous subscription,
        # so requesting a fresh snapshot is safe.
        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="post-close IB position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        final_position = (
            get_mbt_position(
                broker_client
            )
        )

        if (
            final_position
            != EXPECTED_FINAL_POSITION
        ):
            raise RuntimeError(
                "Filled SELL_TO_CLOSE did not flatten "
                "the MBT paper position. "
                f"Observed position: {final_position}."
            )

        return IBPaperCloseResult(
            event_id=event_id,
            broker_order_id=broker_order_id,
            initial_position=initial_position,
            risk_approved=(
                risk_decision.approved
            ),
            projected_position=(
                risk_decision.projected_position
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
            final_position=final_position,
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
    result: IBPaperCloseResult,
) -> None:
    """Print the first paper-close result."""

    if not isinstance(
        result,
        IBPaperCloseResult,
    ):
        raise TypeError(
            "'result' must be an IBPaperCloseResult."
        )

    print()
    print(
        "BTS / IB FIRST PAPER SELL_TO_CLOSE TEST"
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
        f"Initial MBT position:     {result.initial_position}"
    )
    print(
        f"Risk approved:            {result.risk_approved}"
    )
    print(
        "Projected position:       "
        f"{result.projected_position}"
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
            "RESULT: PASS - 1 MBT SELL_TO_CLOSE "
            "completed successfully."
        )
        print(
            "Paper account MBT position is FLAT."
        )

    else:
        print(
            "RESULT: FAIL - paper-close validation failed."
        )

    print()


def main(
    arguments: list[str] | None = None,
) -> int:
    """Run the explicitly armed first paper close."""

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
            "PAPER CLOSE NOT SENT."
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
        / "ib_first_paper_close.db"
    )

    ledger_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    try:
        result = (
            run_paper_close_test(
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