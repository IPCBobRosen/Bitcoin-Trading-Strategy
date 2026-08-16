"""Verify BTS blocks new MBT entries while already long +1.

This harness uses the real +1 MBT paper position intentionally
left open by the keep-position-after-RESET test.

It proves two independent protections:

1. Replay of the original Eagle BUY_TO_OPEN event:
       durable execution ledger rejects duplicate
       broker function is never called

2. Brand-new BUY_TO_OPEN event:
       current broker position = +1
       requested quantity = +1
       projected position = +2
       max_absolute_position = 1
       RiskManager rejects the request

No valid order-submission path exists in this harness.
The paper account must remain exactly +1 MBT throughout.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys

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
from app.ib_execution_client import IBExecutionClient
from app.ib_order_factory import IBOrderFactory
from app.kill_switch import KillSwitch
from app.risk_manager import RiskManager
from app.trading_controls import TradingControls

from scripts.test_ib_paper_keep_position_after_reset import (
    CONNECTION_TIMEOUT_SECONDS,
    CONTRACT_MONTH,
    CURRENCY,
    EXCHANGE,
    HOST,
    PORT,
    CLIENT_ID,
    QUANTITY,
    STOP_LOSS_POINTS,
    SYMBOL,
    TRADING_CLASS,
    get_mbt_position,
    wait_until,
)


SOURCE_LEDGER_PATH = Path(
    "data"
) / "ib_keep_position_after_reset_test.db"

MAX_DAILY_LOSS = Decimal("1000")

ARMING_ARGUMENT = "--confirm-positioned-entry-guard"


@dataclass(frozen=True, slots=True)
class IBAlreadyPositionedEntryGuardResult:
    """Immutable result of the positioned-entry safety test."""

    source_event_id: str
    source_broker_order_id: int

    initial_position: int

    duplicate_rejected: bool
    broker_calls_after_duplicate: int
    position_after_duplicate: int

    new_event_id: str
    new_entry_risk_rejected: bool
    projected_position: int
    broker_calls_after_new_entry: int
    position_after_new_entry: int

    @property
    def successful(self) -> bool:
        """Return True when neither attempted entry reached IB."""

        return (
            self.initial_position == 1
            and self.duplicate_rejected
            and self.broker_calls_after_duplicate == 0
            and self.position_after_duplicate == 1
            and self.new_entry_risk_rejected
            and self.projected_position == 2
            and self.broker_calls_after_new_entry == 0
            and self.position_after_new_entry == 1
        )


class ForbiddenBrokerSubmission:
    """Record any broker call that should never occur."""

    def __init__(self) -> None:
        """Create an unused broker-call sentinel."""

        self._call_count = 0

    @property
    def call_count(self) -> int:
        """Return number of attempted broker submissions."""

        return self._call_count

    def __call__(
        self,
        order_id,
        contract,
        order,
    ) -> None:
        """Fail immediately if BTS reaches broker submission."""

        self._call_count += 1

        raise RuntimeError(
            "Safety violation: positioned-entry guard "
            "reached broker submission."
        )


def load_original_filled_entry(
    ledger: ExecutionLedger,
) -> ExecutionRecord:
    """Load the one valid filled MBT entry from prior live test."""

    if not isinstance(
        ledger,
        ExecutionLedger,
    ):
        raise TypeError(
            "'ledger' must be an ExecutionLedger."
        )

    matches = tuple(
        record
        for record in ledger.all_records()
        if (
            record.symbol == SYMBOL
            and record.intent
            == TradeIntent.BUY_TO_OPEN.value
            and record.quantity == QUANTITY
            and record.status
            is ExecutionStatus.FILLED
            and record.broker_order_id
            is not None
        )
    )

    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one durable FILLED "
            "BUY_TO_OPEN 1 MBT source event."
        )

    return matches[0]


def reconstruct_original_request(
    record: ExecutionRecord,
) -> TradeRequest:
    """Reconstruct the previously processed Eagle entry event."""

    if not isinstance(
        record,
        ExecutionRecord,
    ):
        raise TypeError(
            "'record' must be an ExecutionRecord."
        )

    if (
        record.status
        is not ExecutionStatus.FILLED
    ):
        raise ValueError(
            "Original execution record must be FILLED."
        )

    if (
        record.intent
        != TradeIntent.BUY_TO_OPEN.value
    ):
        raise ValueError(
            "Original execution must be BUY_TO_OPEN."
        )

    return TradeRequest(
        event_id=record.event_id,
        signal_id=record.signal_id,
        timestamp=record.created_at,
        environment=Environment.STAGING,
        intent=TradeIntent.BUY_TO_OPEN,
        symbol=record.symbol,
        quantity=record.quantity,
        stop_loss_points=STOP_LOSS_POINTS,
    )


def build_new_entry_request() -> TradeRequest:
    """Build a brand-new BUY_TO_OPEN event."""

    event_id = (
        "paper-positioned-new-entry-"
        + datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%dT%H%M%S%fZ"
        )
    )

    return TradeRequest(
        event_id=event_id,
        signal_id="paper-positioned-new-signal-001",
        timestamp=datetime.now(
            timezone.utc
        ),
        environment=Environment.STAGING,
        intent=TradeIntent.BUY_TO_OPEN,
        symbol=SYMBOL,
        quantity=QUANTITY,
        stop_loss_points=STOP_LOSS_POINTS,
    )


def require_long_one(
    broker_client: IBBrokerClient,
) -> int:
    """Require broker position to be exactly +1 MBT."""

    position = get_mbt_position(
        broker_client
    )

    if position != 1:
        raise RuntimeError(
            "Already-positioned entry-guard test "
            "requires exactly +1 MBT. "
            f"Observed position: {position}."
        )

    return position


def attempt_duplicate_replay(
    *,
    execution_client: IBExecutionClient,
    original_request: TradeRequest,
    candidate_order_id: int,
) -> bool:
    """Require replay of original event to fail before broker call."""

    if not isinstance(
        execution_client,
        IBExecutionClient,
    ):
        raise TypeError(
            "'execution_client' must be an IBExecutionClient."
        )

    if not isinstance(
        original_request,
        TradeRequest,
    ):
        raise TypeError(
            "'original_request' must be a TradeRequest."
        )

    try:
        execution_client.submit(
            original_request,
            contract_month=CONTRACT_MONTH,
            broker_order_id=candidate_order_id,
        )

    except ValueError:
        return True

    raise RuntimeError(
        "Safety violation: original Eagle event "
        "was not rejected as a duplicate."
    )


def run_already_positioned_entry_guard_test(
    *,
    armed: bool,
    source_ledger_path: str | Path = SOURCE_LEDGER_PATH,
) -> IBAlreadyPositionedEntryGuardResult:
    """Run real broker-position entry protection test."""

    if not isinstance(
        armed,
        bool,
    ):
        raise TypeError(
            "'armed' must be a bool."
        )

    if not armed:
        raise RuntimeError(
            "Already-positioned entry-guard harness "
            "is not armed."
        )

    source_ledger = ExecutionLedger(
        source_ledger_path
    )

    original_record = (
        load_original_filled_entry(
            source_ledger
        )
    )

    original_request = (
        reconstruct_original_request(
            original_record
        )
    )

    broker_client = IBBrokerClient()

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

    app = IBApiPositionApp(
        broker_client,
        execution_ledger=source_ledger,
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

    forbidden_broker_submission = (
        ForbiddenBrokerSubmission()
    )

    duplicate_guard = DuplicateOrderGuard()

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
        execution_ledger=source_ledger,
        place_order_function=(
            forbidden_broker_submission
        ),
    )

    try:
        manager.connect()

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="initial +1 MBT position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        initial_position = (
            require_long_one(
                broker_client
            )
        )

        print()
        print(
            "INITIAL BROKER STATE VERIFIED:"
        )
        print(
            "Paper account is LONG exactly +1 MBT."
        )
        print(
            "No additional exposure will be permitted."
        )

        # -----------------------------------------------------
        # TEST 1:
        # Replay the real original Eagle event.
        # -----------------------------------------------------

        duplicate_candidate_order_id = (
            app.order_id_allocator.allocate()
        )

        duplicate_rejected = (
            attempt_duplicate_replay(
                execution_client=execution_client,
                original_request=original_request,
                candidate_order_id=(
                    duplicate_candidate_order_id
                ),
            )
        )

        broker_calls_after_duplicate = (
            forbidden_broker_submission.call_count
        )

        if broker_calls_after_duplicate != 0:
            raise RuntimeError(
                "Duplicate replay reached broker submission."
            )

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="post-duplicate position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        position_after_duplicate = (
            require_long_one(
                broker_client
            )
        )

        print()
        print(
            "DUPLICATE REPLAY TEST: PASS"
        )
        print(
            f"Original event: {original_record.event_id}"
        )
        print(
            "Duplicate rejected before broker submission."
        )
        print(
            "Broker position remains +1 MBT."
        )

        # -----------------------------------------------------
        # TEST 2:
        # Brand-new BUY_TO_OPEN while already +1.
        # -----------------------------------------------------

        new_request = (
            build_new_entry_request()
        )

        risk_decision = (
            risk_manager.evaluate(
                new_request,
                current_position=1,
            )
        )

        if risk_decision.approved:
            raise RuntimeError(
                "Safety violation: RiskManager approved "
                "a BUY_TO_OPEN that would increase "
                "+1 MBT to +2 MBT."
            )

        projected_position = (
            risk_decision.projected_position
        )

        if projected_position != 2:
            raise RuntimeError(
                "Unexpected projected position. "
                f"Expected +2, observed "
                f"{projected_position}."
            )

        broker_calls_after_new_entry = (
            forbidden_broker_submission.call_count
        )

        if broker_calls_after_new_entry != 0:
            raise RuntimeError(
                "Rejected new entry reached broker submission."
            )

        manager.request_position_snapshot()

        wait_until(
            lambda: broker_client.snapshot_complete,
            description="final +1 MBT position snapshot",
            timeout_seconds=(
                CONNECTION_TIMEOUT_SECONDS
            ),
        )

        position_after_new_entry = (
            require_long_one(
                broker_client
            )
        )

        print()
        print(
            "NEW ENTRY POSITION-LIMIT TEST: PASS"
        )
        print(
            "Current position:   +1 MBT"
        )
        print(
            "Requested entry:    BUY 1 MBT"
        )
        print(
            "Projected position: +2 MBT"
        )
        print(
            "Maximum permitted:  +/-1 MBT"
        )
        print(
            "RiskManager rejected the new entry."
        )
        print(
            "Broker position remains +1 MBT."
        )

        return IBAlreadyPositionedEntryGuardResult(
            source_event_id=(
                original_record.event_id
            ),
            source_broker_order_id=(
                original_record.broker_order_id
            ),
            initial_position=(
                initial_position
            ),
            duplicate_rejected=(
                duplicate_rejected
            ),
            broker_calls_after_duplicate=(
                broker_calls_after_duplicate
            ),
            position_after_duplicate=(
                position_after_duplicate
            ),
            new_event_id=(
                new_request.event_id
            ),
            new_entry_risk_rejected=(
                not risk_decision.approved
            ),
            projected_position=(
                projected_position
            ),
            broker_calls_after_new_entry=(
                broker_calls_after_new_entry
            ),
            position_after_new_entry=(
                position_after_new_entry
            ),
        )

    finally:
        manager.disconnect()


def print_result(
    result: IBAlreadyPositionedEntryGuardResult,
) -> None:
    """Print already-positioned entry-guard result."""

    if not isinstance(
        result,
        IBAlreadyPositionedEntryGuardResult,
    ):
        raise TypeError(
            "'result' must be an "
            "IBAlreadyPositionedEntryGuardResult."
        )

    print()
    print(
        "BTS / IB ALREADY-POSITIONED ENTRY GUARD TEST"
    )
    print(
        "================================================"
    )
    print(
        f"Original broker order ID:      "
        f"{result.source_broker_order_id}"
    )
    print(
        f"Initial MBT position:          "
        f"{result.initial_position}"
    )
    print(
        f"Duplicate rejected:            "
        f"{result.duplicate_rejected}"
    )
    print(
        f"Broker calls after duplicate:  "
        f"{result.broker_calls_after_duplicate}"
    )
    print(
        f"Position after duplicate:      "
        f"{result.position_after_duplicate}"
    )
    print(
        f"New entry risk rejected:       "
        f"{result.new_entry_risk_rejected}"
    )
    print(
        f"Projected new position:        "
        f"{result.projected_position}"
    )
    print(
        f"Broker calls after new entry:  "
        f"{result.broker_calls_after_new_entry}"
    )
    print(
        f"Final MBT position:            "
        f"{result.position_after_new_entry}"
    )
    print(
        "================================================"
    )

    if result.successful:
        print(
            "RESULT: PASS - duplicate and new-entry "
            "protections prevented +2 MBT exposure."
        )
        print(
            "IMPORTANT: Paper account remains "
            "LONG +1 MBT."
        )

    else:
        print(
            "RESULT: FAIL - already-positioned "
            "entry protection failed."
        )

    print()


def main(
    arguments: list[str] | None = None,
) -> int:
    """Run explicitly armed positioned-entry guard test."""

    if arguments is None:
        arguments = sys.argv[1:]

    if arguments != [
        ARMING_ARGUMENT,
    ]:
        print()
        print(
            "POSITIONED-ENTRY GUARD TEST NOT STARTED."
        )
        print(
            "Required argument:"
        )
        print(
            f"    {ARMING_ARGUMENT}"
        )
        print()

        return 2

    try:
        result = (
            run_already_positioned_entry_guard_test(
                armed=True
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
            "Inspect the IB paper position and "
            "durable execution ledger first."
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