"""Controlled matching Eagle exit -> IB paper test.

This harness exists for one narrow purpose:

    An earlier real Eagle STAGING fund.entry opened exactly +1 MBT
    in the TWS paper account.

    This harness waits for the matching fund.exit for that exact
    Eagle signal and permits exactly one SELL_TO_CLOSE paper order.

The harness deliberately refuses to execute:

- any new Eagle entry;
- any exit for another signal;
- any non-BTC signal;
- any quantity other than 1 MBT;
- any state other than the known +1 MBT long position;
- more than one broker submission.

IMPORTANT OBSERVE-ONLY RULE:

Observe-only mode does NOT mutate:

- EventProcessor / processed-event state;
- Eagle durable sequence cursor;
- HeartbeatProcessor state;
- TradeCoordinator lifecycle state;
- execution ledger;
- broker position.

Only armed mode may advance durable Eagle state or lifecycle state.

Keep TWS Read-Only API enabled during development/testing.
Disable Read-Only only for the final explicitly armed exit test.
"""

import argparse
import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
import os
from pathlib import Path
import time

from app.communications.eagle_client import EagleClient
from app.communications.eagle_heartbeat import EagleHeartbeat
from app.communications.eagle_hello import EagleHello
from app.communications.eagle_trade_adapter import (
    EagleTradeAdaptStatus,
    EagleTradeAdapter,
)
from app.communications.incoming_event import IncomingLifecycleEvent
from app.communications.protocol import (
    Environment,
    TradeIntent,
)
from app.daily_loss_guard import DailyLossGuard
from app.duplicate_order_guard import DuplicateOrderGuard
from app.event_processor import (
    EventProcessStatus,
    EventProcessor,
)
from app.event_store import EventStore
from app.execution_ledger import (
    ExecutionLedger,
    ExecutionRecord,
    ExecutionStatus,
)
from app.heartbeat_processor import HeartbeatProcessor
from app.ib_api_position_app import IBApiPositionApp
from app.ib_broker_client import IBBrokerClient
from app.ib_connection_manager import IBConnectionManager
from app.ib_execution_client import IBExecutionClient
from app.ib_order_factory import IBOrderFactory
from app.ib_trading_readiness import IBTradingReadiness
from app.kill_switch import KillSwitch
from app.risk_manager import RiskManager
from app.signal_lifecycle_guard import (
    SignalLifecycleGuard,
    SignalLifecycleState,
)
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


TARGET_SIGNAL_ID = (
    "bt-1778737920000-BTCUSDT-43"
)

TARGET_ENTRY_EVENT_ID = (
    TARGET_SIGNAL_ID
    + ":entry"
)

EXPECTED_ENTRY_INTENT = (
    TradeIntent.BUY_TO_OPEN
)

EXPECTED_EXIT_INTENT = (
    TradeIntent.SELL_TO_CLOSE
)


EAGLE_URI = (
    "wss://tracer.eagleailabs.com/"
    "ipc-api/ipc/v1/fund/stream"
)

EAGLE_API_KEY_ENVIRONMENT_VARIABLE = (
    "BTS_EAGLE_API_KEY"
)


IB_HOST = "127.0.0.1"
IB_PORT = 7497
IB_CLIENT_ID = 1

SYMBOL = "MBT"
EXPECTED_LOCAL_SYMBOL = "MBTQ6"

EXCHANGE = "CME"
CURRENCY = "USD"
TRADING_CLASS = "MBT"

CONTRACT_MONTH = "20260828"

PAPER_QUANTITY = 1
STOP_LOSS_POINTS = Decimal("500")

MAX_DAILY_LOSS = Decimal("1000")
MAX_ORDER_QUANTITY = 1
MAX_ABSOLUTE_POSITION = 1


DEFAULT_EVENT_DATABASE = (
    Path("data")
    / "real_eagle_to_ib_bridge_events.db"
)

DEFAULT_LIFECYCLE_DATABASE = (
    Path("data")
    / "real_eagle_to_ib_bridge_signals.db"
)

DEFAULT_EXECUTION_LEDGER = (
    Path("data")
    / "real_eagle_to_ib_bridge_execution.db"
)


CONNECTION_TIMEOUT_SECONDS = 10.0
EXECUTION_TIMEOUT_SECONDS = 20.0

DEFAULT_MAX_MESSAGES = 100

ARMING_ARGUMENT = (
    "--confirm-paper-exit"
)


@dataclass(frozen=True, slots=True)
class MatchingExitResult:
    """Immutable result of the controlled matching-exit test."""

    armed: bool

    eagle_hello_received: bool
    eagle_environment_staging: bool

    starting_lifecycle_state: SignalLifecycleState
    entry_execution_status: ExecutionStatus

    initial_position_count: int
    initial_mbt_position: int

    target_exit_seen: bool
    target_exit_event_id: str | None
    target_exit_intent: str | None

    ignored_entry_count: int
    ignored_other_exit_count: int
    ignored_non_btc_count: int

    risk_approved: bool
    readiness_passed: bool

    broker_submission_count: int
    broker_order_id: int | None

    final_execution_status: ExecutionStatus | None
    final_mbt_position: int

    final_lifecycle_state: SignalLifecycleState | None

    kill_switch_active: bool

    final_eagle_cursor: int | None

    @property
    def successful(self) -> bool:
        """Return True when the requested exit-test mode succeeded."""

        common_success = (
            self.eagle_hello_received
            and self.eagle_environment_staging
            and self.starting_lifecycle_state
            is SignalLifecycleState.LONG_OPEN
            and self.entry_execution_status
            is ExecutionStatus.FILLED
            and self.initial_position_count == 1
            and self.initial_mbt_position == 1
            and not self.kill_switch_active
        )

        if not common_success:
            return False

        if not self.armed:
            return (
                self.broker_submission_count == 0
                and self.broker_order_id is None
                and self.final_mbt_position == 1
            )

        return (
            self.target_exit_seen
            and self.target_exit_event_id is not None
            and self.target_exit_intent
            == TradeIntent.SELL_TO_CLOSE.value
            and self.risk_approved
            and self.readiness_passed
            and self.broker_submission_count == 1
            and self.broker_order_id is not None
            and self.final_execution_status
            is ExecutionStatus.FILLED
            and self.final_mbt_position == 0
            and self.final_lifecycle_state
            is SignalLifecycleState.CLOSED
            and not self.kill_switch_active
        )


def wait_until(
    condition: Callable[[], bool],
    *,
    description: str,
    timeout_seconds: float,
    poll_interval_seconds: float = 0.05,
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

    deadline = (
        time.monotonic()
        + float(timeout_seconds)
    )

    while not condition():
        if time.monotonic() >= deadline:
            raise TimeoutError(
                "Timed out waiting for "
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
    """Wait for matching exit to reach terminal execution state."""

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
        description=(
            "matching Eagle paper exit resolution"
        ),
        timeout_seconds=timeout_seconds,
    )

    if kill_switch.active:
        raise RuntimeError(
            "BTS kill switch activated while "
            "waiting for matching paper exit: "
            f"{kill_switch.reason}"
        )

    record = execution_ledger.get(
        event_id
    )

    if record is None:
        raise RuntimeError(
            "Matching exit disappeared from "
            "the execution ledger."
        )

    return record


def get_mbt_position(
    broker_client: IBBrokerClient,
) -> int:
    """Return current signed MBT position."""

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
        normalized_symbol = (
            position.symbol
            .strip()
            .upper()
        )

        if normalized_symbol in {
            SYMBOL,
            EXPECTED_LOCAL_SYMBOL,
        }:
            total += position.quantity

    return total


def refresh_position_snapshot(
    *,
    app: IBApiPositionApp,
    manager: IBConnectionManager,
    broker_client: IBBrokerClient,
) -> None:
    """Obtain a fresh completed broker-position snapshot."""

    if app.position_request_active:
        app.cancel_position_updates()

    manager.request_position_snapshot()

    wait_until(
        lambda: broker_client.snapshot_complete,
        description=(
            "IB position snapshot completion"
        ),
        timeout_seconds=(
            CONNECTION_TIMEOUT_SECONDS
        ),
    )


def require_exact_long_position(
    broker_client: IBBrokerClient,
) -> None:
    """Require exactly one +1 MBT paper position and nothing else."""

    positions = (
        broker_client.get_raw_positions()
    )

    if len(positions) != 1:
        raise RuntimeError(
            "Matching-exit test requires exactly "
            "one paper-account position."
        )

    mbt_position = get_mbt_position(
        broker_client
    )

    if mbt_position != 1:
        raise RuntimeError(
            "Matching-exit test requires exactly "
            "+1 MBT before the close."
        )


def require_flat_after_exit(
    broker_client: IBBrokerClient,
) -> None:
    """Require the paper account to be completely flat."""

    positions = (
        broker_client.get_raw_positions()
    )

    if positions:
        raise RuntimeError(
            "Matching exit filled but the paper "
            "account is not completely flat."
        )

    if get_mbt_position(
        broker_client
    ) != 0:
        raise RuntimeError(
            "Matching exit did not return MBT "
            "position to zero."
        )


def validate_existing_entry_record(
    execution_ledger: ExecutionLedger,
) -> ExecutionRecord:
    """Require the durable entry execution to be expected."""

    record = execution_ledger.get(
        TARGET_ENTRY_EVENT_ID
    )

    if record is None:
        raise RuntimeError(
            "Required filled entry execution "
            "record does not exist."
        )

    if record.signal_id != TARGET_SIGNAL_ID:
        raise RuntimeError(
            "Existing entry execution has "
            "unexpected signal_id."
        )

    if record.symbol != SYMBOL:
        raise RuntimeError(
            "Existing entry execution is not MBT."
        )

    if record.intent != EXPECTED_ENTRY_INTENT.value:
        raise RuntimeError(
            "Existing entry execution is not "
            "BUY_TO_OPEN."
        )

    if record.quantity != PAPER_QUANTITY:
        raise RuntimeError(
            "Existing entry execution quantity "
            "is not exactly 1."
        )

    if record.status is not ExecutionStatus.FILLED:
        raise RuntimeError(
            "Existing entry execution is not FILLED."
        )

    if record.broker_order_id is None:
        raise RuntimeError(
            "Existing filled entry has no "
            "broker order ID."
        )

    return record


def validate_matching_exit_request(
    trade_request,
) -> None:
    """Enforce exact matching-exit execution policy."""

    if (
        trade_request.environment
        is not Environment.STAGING
    ):
        raise RuntimeError(
            "Matching exit requires "
            "Environment.STAGING."
        )

    if (
        trade_request.signal_id
        != TARGET_SIGNAL_ID
    ):
        raise RuntimeError(
            "Matching exit has unexpected signal_id."
        )

    if trade_request.symbol != SYMBOL:
        raise RuntimeError(
            "Matching exit permits MBT only."
        )

    if (
        trade_request.quantity
        != PAPER_QUANTITY
    ):
        raise RuntimeError(
            "Matching exit permits exactly "
            "1 MBT."
        )

    if (
        trade_request.intent
        is not EXPECTED_EXIT_INTENT
    ):
        raise RuntimeError(
            "Matching exit permits only "
            "SELL_TO_CLOSE."
        )


async def run_matching_exit_test(
    *,
    armed: bool,
    api_key: str,
    event_database_path: str | Path,
    lifecycle_database_path: str | Path,
    execution_ledger_path: str | Path,
    max_messages: int,
) -> MatchingExitResult:
    """Run one controlled matching Eagle exit test."""

    if not isinstance(
        armed,
        bool,
    ):
        raise TypeError(
            "'armed' must be a bool."
        )

    if (
        not isinstance(max_messages, int)
        or isinstance(max_messages, bool)
        or max_messages <= 0
    ):
        raise ValueError(
            "'max_messages' must be a positive integer."
        )

    event_store = EventStore(
        event_database_path
    )

    event_processor = EventProcessor(
        event_store
    )

    heartbeat_processor = HeartbeatProcessor(
        event_store
    )

    lifecycle_guard = SignalLifecycleGuard(
        lifecycle_database_path
    )

    starting_lifecycle_state = (
        lifecycle_guard.get_state(
            TARGET_SIGNAL_ID
        )
    )

    if (
        starting_lifecycle_state
        is not SignalLifecycleState.LONG_OPEN
    ):
        raise RuntimeError(
            "Matching-exit test requires target "
            "signal lifecycle to be LONG_OPEN."
        )

    adapter = EagleTradeAdapter(
        lifecycle_guard
    )

    execution_ledger = ExecutionLedger(
        execution_ledger_path
    )

    entry_record = (
        validate_existing_entry_record(
            execution_ledger
        )
    )

    for record in (
        execution_ledger.all_records()
    ):
        if not record.terminal:
            raise RuntimeError(
                "Matching-exit test found unresolved "
                "execution state."
            )

    kill_switch = KillSwitch()

    trading_controls = TradingControls(
        symbol=SYMBOL,
        quantity=PAPER_QUANTITY,
        stop_loss_points=(
            STOP_LOSS_POINTS
        ),
    )

    trading_controls.resume()

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
        max_order_quantity=(
            MAX_ORDER_QUANTITY
        ),
        max_absolute_position=(
            MAX_ABSOLUTE_POSITION
        ),
    )

    coordinator = TradeCoordinator(
        controls=trading_controls,
        signal_lifecycle_guard=(
            lifecycle_guard
        ),
    )

    duplicate_guard = DuplicateOrderGuard()

    broker_client = IBBrokerClient()

    app = IBApiPositionApp(
        broker_client,
        execution_ledger=(
            execution_ledger
        ),
        kill_switch=(
            kill_switch
        ),
    )

    manager = IBConnectionManager(
        app,
        host=IB_HOST,
        port=IB_PORT,
        client_id=IB_CLIENT_ID,
        connection_timeout_seconds=(
            CONNECTION_TIMEOUT_SECONDS
        ),
    )

    eagle_hello_received = False
    eagle_environment_staging = False

    initial_position_count = 0
    initial_mbt_position = 0

    target_exit_seen = False
    target_exit_event_id: str | None = None
    target_exit_intent: str | None = None

    ignored_entry_count = 0
    ignored_other_exit_count = 0
    ignored_non_btc_count = 0

    risk_approved = False
    readiness_passed = False

    broker_submission_count = 0
    broker_order_id: int | None = None

    final_execution_status: (
        ExecutionStatus | None
    ) = None

    final_mbt_position = 1

    messages_observed = 0

    original_eagle_cursor = (
        event_store.get_last_seq()
    )

    try:
        manager.connect()

        wait_until(
            lambda: app.api_ready.ready,
            description=(
                "IB nextValidId handshake"
            ),
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

        refresh_position_snapshot(
            app=app,
            manager=manager,
            broker_client=broker_client,
        )

        initial_positions = (
            broker_client.get_raw_positions()
        )

        initial_position_count = len(
            initial_positions
        )

        initial_mbt_position = (
            get_mbt_position(
                broker_client
            )
        )

        require_exact_long_position(
            broker_client
        )

        print()
        print(
            "MATCHING EXIT PRE-FLIGHT"
        )
        print("=" * 72)
        print(
            f"Target signal:             "
            f"{TARGET_SIGNAL_ID}"
        )
        print(
            f"Lifecycle state:           "
            f"{starting_lifecycle_state}"
        )
        print(
            f"Entry execution status:    "
            f"{entry_record.status.value}"
        )
        print(
            f"Entry broker order ID:     "
            f"{entry_record.broker_order_id}"
        )
        print(
            f"TWS API ready:             "
            f"{app.api_ready.ready}"
        )
        print(
            f"Paper position count:      "
            f"{initial_position_count}"
        )
        print(
            f"Initial MBT position:      "
            f"{initial_mbt_position}"
        )
        print(
            f"Broker submission armed:   "
            f"{armed}"
        )
        print("=" * 72)

        eagle_client = EagleClient(
            uri=EAGLE_URI,
            api_key=api_key,
            since_seq=(
                original_eagle_cursor
            ),
        )

        print()
        print(
            "MATCHING EAGLE EXIT -> IB PAPER"
        )
        print("=" * 72)
        print(
            f"Eagle URI: "
            f"{eagle_client._connection_uri()}"
        )
        print(
            "ONLY THIS SIGNAL MAY CLOSE:"
        )
        print(
            f"    {TARGET_SIGNAL_ID}"
        )
        print(
            "Required intent: SELL_TO_CLOSE"
        )
        print(
            "Maximum broker submissions: 1"
        )

        if armed:
            print(
                "MODE: ARMED FOR ONE MATCHING PAPER EXIT"
            )
        else:
            print(
                "MODE: OBSERVE ONLY - EXIT WILL NOT BE SENT"
            )
            print(
                "OBSERVE MODE WILL NOT MUTATE DURABLE STATE."
            )

        print("=" * 72)

        async for message in (
            eagle_client.listen()
        ):
            messages_observed += 1

            print()
            print("-" * 72)

            if isinstance(
                message,
                EagleHello,
            ):
                eagle_hello_received = True

                eagle_environment_staging = (
                    message.environment.value
                    == "staging"
                )

                print(
                    "fund.hello received."
                )
                print(
                    f"Environment:       "
                    f"{message.environment.value}"
                )
                print(
                    f"Replay count:      "
                    f"{message.replay_count}"
                )
                print(
                    f"Eagle open count:  "
                    f"{message.open_count}"
                )
                print(
                    f"Server last_seq:   "
                    f"{message.last_seq}"
                )
                print(
                    f"Requested since:   "
                    f"{message.since_seq}"
                )

                if not eagle_environment_staging:
                    raise RuntimeError(
                        "Safety violation: matching-exit "
                        "harness connected to non-STAGING "
                        "Eagle environment."
                    )

            elif isinstance(
                message,
                EagleHeartbeat,
            ):
                if armed:
                    heartbeat_processor.process(
                        message
                    )

                    print(
                        f"fund.heartbeat seq "
                        f"{message.seq}"
                    )
                    print(
                        f"Durable cursor: "
                        f"{event_store.get_last_seq()}"
                    )

                else:
                    print(
                        f"fund.heartbeat seq "
                        f"{message.seq}"
                    )
                    print(
                        "OBSERVE ONLY - heartbeat "
                        "was NOT persisted."
                    )

            elif isinstance(
                message,
                IncomingLifecycleEvent,
            ):
                print(
                    f"Lifecycle: "
                    f"{message.message_type}"
                )
                print(
                    f"Seq:       "
                    f"{message.seq}"
                )
                print(
                    f"Signal ID: "
                    f"{message.signal_id}"
                )

                # -------------------------------------------------
                # OBSERVE-ONLY MODE
                #
                # Absolutely no EventProcessor or TradeCoordinator
                # calls are allowed here.
                # -------------------------------------------------

                if not armed:
                    print(
                        "Event status: OBSERVED ONLY"
                    )

                    if (
                        message.message_type
                        == "fund.entry"
                    ):
                        ignored_entry_count += 1

                        raw_signal = (
                            message.payload.get(
                                "signal"
                            )
                        )

                        eagle_symbol = None

                        if isinstance(
                            raw_signal,
                            dict,
                        ):
                            eagle_symbol = (
                                raw_signal.get(
                                    "symbol"
                                )
                            )

                        if (
                            isinstance(
                                eagle_symbol,
                                str,
                            )
                            and eagle_symbol.strip().upper()
                            != "BTCUSDT"
                        ):
                            ignored_non_btc_count += 1

                        print(
                            "ENTRY HARD STOP - this harness "
                            "will not open another position."
                        )

                    elif (
                        message.message_type
                        != "fund.exit"
                    ):
                        print(
                            "Unsupported lifecycle type ignored."
                        )

                    elif (
                        message.signal_id
                        != TARGET_SIGNAL_ID
                    ):
                        ignored_other_exit_count += 1

                        print(
                            "UNRELATED EXIT HARD STOP."
                        )
                        print(
                            "Exit does not belong to the "
                            "target open MBT position."
                        )

                    else:
                        target_exit_seen = True

                        print(
                            "MATCHING TARGET EXIT RECEIVED."
                        )

                        adapt_result = (
                            adapter.adapt(
                                message
                            )
                        )

                        print(
                            "Adapter status: "
                            f"{adapt_result.status.value}"
                        )

                        if (
                            adapt_result.status
                            is not EagleTradeAdaptStatus.ADAPTED
                        ):
                            raise RuntimeError(
                                "Matching target exit did not "
                                "adapt successfully."
                            )

                        normalized_event = (
                            adapt_result.event
                        )

                        if normalized_event is None:
                            raise RuntimeError(
                                "Matching exit adapter returned "
                                "no normalized event."
                            )

                        target_exit_event_id = (
                            normalized_event.event_id
                        )

                        target_exit_intent = (
                            normalized_event.payload[
                                "intent"
                            ]
                        )

                        print(
                            "Normalized intent: "
                            f"{target_exit_intent}"
                        )

                        if (
                            target_exit_intent
                            != EXPECTED_EXIT_INTENT.value
                        ):
                            raise RuntimeError(
                                "Matching Eagle exit did not "
                                "normalize to SELL_TO_CLOSE."
                            )

                        print(
                            "OBSERVE-ONLY HARD STOP."
                        )
                        print(
                            "Matching SELL_TO_CLOSE validated."
                        )
                        print(
                            "TradeCoordinator was NOT called."
                        )
                        print(
                            "Durable lifecycle remains LONG_OPEN."
                        )
                        print(
                            "Matching SELL_TO_CLOSE was "
                            "NOT sent to Interactive Brokers."
                        )

                        break

                # -------------------------------------------------
                # ARMED MODE
                # -------------------------------------------------

                else:
                    event_result = (
                        event_processor.process(
                            message
                        )
                    )

                    print(
                        "Event status: "
                        f"{event_result.status.value}"
                    )

                    if (
                        event_result.status
                        is not EventProcessStatus.ACCEPTED
                    ):
                        print(
                            "Duplicate/out-of-sequence "
                            "event ignored."
                        )

                    elif (
                        message.message_type
                        == "fund.entry"
                    ):
                        ignored_entry_count += 1

                        raw_signal = (
                            message.payload.get(
                                "signal"
                            )
                        )

                        eagle_symbol = None

                        if isinstance(
                            raw_signal,
                            dict,
                        ):
                            eagle_symbol = (
                                raw_signal.get(
                                    "symbol"
                                )
                            )

                        if (
                            isinstance(
                                eagle_symbol,
                                str,
                            )
                            and eagle_symbol.strip().upper()
                            != "BTCUSDT"
                        ):
                            ignored_non_btc_count += 1

                        print(
                            "ENTRY HARD STOP - this harness "
                            "will not open another position."
                        )

                    elif (
                        message.message_type
                        != "fund.exit"
                    ):
                        print(
                            "Unsupported lifecycle type ignored."
                        )

                    elif (
                        message.signal_id
                        != TARGET_SIGNAL_ID
                    ):
                        ignored_other_exit_count += 1

                        print(
                            "UNRELATED EXIT HARD STOP."
                        )
                        print(
                            "Exit does not belong to the "
                            "target open MBT position."
                        )

                    else:
                        target_exit_seen = True

                        print(
                            "MATCHING TARGET EXIT RECEIVED."
                        )

                        adapt_result = (
                            adapter.adapt(
                                message
                            )
                        )

                        print(
                            "Adapter status: "
                            f"{adapt_result.status.value}"
                        )

                        if (
                            adapt_result.status
                            is not EagleTradeAdaptStatus.ADAPTED
                        ):
                            raise RuntimeError(
                                "Matching target exit did not "
                                "adapt into a BTS TradeRequest."
                            )

                        normalized_event = (
                            adapt_result.event
                        )

                        if normalized_event is None:
                            raise RuntimeError(
                                "Matching exit adapter returned "
                                "no normalized event."
                            )

                        target_exit_event_id = (
                            normalized_event.event_id
                        )

                        target_exit_intent = (
                            normalized_event.payload[
                                "intent"
                            ]
                        )

                        print(
                            "Normalized intent: "
                            f"{target_exit_intent}"
                        )

                        if (
                            target_exit_intent
                            != EXPECTED_EXIT_INTENT.value
                        ):
                            raise RuntimeError(
                                "Matching Eagle exit did not "
                                "normalize to SELL_TO_CLOSE."
                            )

                        decision = (
                            coordinator.process_event(
                                normalized_event
                            )
                        )

                        print(
                            "Trade decision approved: "
                            f"{decision.approved}"
                        )
                        print(
                            "Trade decision reason:   "
                            f"{decision.reason}"
                        )

                        if not decision.approved:
                            raise RuntimeError(
                                "TradeCoordinator rejected "
                                "the matching close."
                            )

                        trade_request = (
                            decision.trade_request
                        )

                        if trade_request is None:
                            raise RuntimeError(
                                "Approved matching exit had "
                                "no TradeRequest."
                            )

                        validate_matching_exit_request(
                            trade_request
                        )

                        print(
                            "MATCHING CLOSE REQUEST:"
                        )
                        print(
                            f"  Signal:   "
                            f"{trade_request.signal_id}"
                        )
                        print(
                            f"  Intent:   "
                            f"{trade_request.intent.value}"
                        )
                        print(
                            f"  Symbol:   "
                            f"{trade_request.symbol}"
                        )
                        print(
                            f"  Quantity: "
                            f"{trade_request.quantity}"
                        )

                        if (
                            broker_submission_count
                            >= 1
                        ):
                            raise RuntimeError(
                                "Safety violation: matching-exit "
                                "one-order limit already reached."
                            )

                        refresh_position_snapshot(
                            app=app,
                            manager=manager,
                            broker_client=broker_client,
                        )

                        require_exact_long_position(
                            broker_client
                        )

                        current_position = (
                            get_mbt_position(
                                broker_client
                            )
                        )

                        risk_decision = (
                            risk_manager.evaluate(
                                trade_request,
                                current_position=(
                                    current_position
                                ),
                            )
                        )

                        risk_approved = (
                            risk_decision.approved
                        )

                        print(
                            f"Risk approved: "
                            f"{risk_approved}"
                        )
                        print(
                            "Projected position: "
                            f"{risk_decision.projected_position}"
                        )

                        if not risk_approved:
                            raise RuntimeError(
                                "RiskManager rejected the "
                                "matching SELL_TO_CLOSE: "
                                f"{risk_decision.reason}"
                            )

                        if (
                            risk_decision.projected_position
                            != 0
                        ):
                            raise RuntimeError(
                                "Matching close does not "
                                "project broker position to zero."
                            )

                        lifecycle_before_submission = (
                            lifecycle_guard.get_state(
                                TARGET_SIGNAL_ID
                            )
                        )

                        if (
                            lifecycle_before_submission
                            is not SignalLifecycleState.CLOSED
                        ):
                            raise RuntimeError(
                                "TradeCoordinator did not move "
                                "target lifecycle to CLOSED."
                            )

                        unresolved_records = tuple(
                            record
                            for record in (
                                execution_ledger.all_records()
                            )
                            if not record.terminal
                        )

                        execution_state_clear = (
                            len(
                                unresolved_records
                            )
                            == 0
                        )

                        positions_reconciled = (
                            current_position == 1
                        )

                        readiness = (
                            IBTradingReadiness(
                                api_ready=(
                                    app.api_ready
                                ),
                                order_id_allocator=(
                                    app.order_id_allocator
                                ),
                                broker_client=(
                                    broker_client
                                ),
                                trading_controls=(
                                    trading_controls
                                ),
                                kill_switch=(
                                    kill_switch
                                ),
                            )
                        )

                        readiness_result = (
                            readiness.require_ready(
                                positions_reconciled=(
                                    positions_reconciled
                                ),
                                execution_state_clear=(
                                    execution_state_clear
                                ),
                            )
                        )

                        readiness_passed = (
                            readiness_result.ready
                        )

                        print(
                            "IB readiness passed: "
                            f"{readiness_passed}"
                        )

                        order_factory = IBOrderFactory(
                            exchange=EXCHANGE,
                            currency=CURRENCY,
                            trading_class=(
                                TRADING_CLASS
                            ),
                            order_type="MKT",
                            time_in_force="DAY",
                            transmit=True,
                        )

                        execution_client = (
                            IBExecutionClient(
                                order_factory=(
                                    order_factory
                                ),
                                duplicate_guard=(
                                    duplicate_guard
                                ),
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

                        print()
                        print(
                            "=" * 72
                        )
                        print(
                            "ONE MATCHING PAPER EXIT AUTHORIZED"
                        )
                        print(
                            "=" * 72
                        )
                        print(
                            f"Eagle event: "
                            f"{trade_request.event_id}"
                        )
                        print(
                            f"Signal ID:   "
                            f"{trade_request.signal_id}"
                        )
                        print(
                            f"Intent:      "
                            f"{trade_request.intent.value}"
                        )
                        print(
                            "Quantity:    1 MBT"
                        )
                        print(
                            f"IB order ID: "
                            f"{broker_order_id}"
                        )
                        print(
                            "=" * 72
                        )

                        submission = (
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

                        broker_submission_count += 1

                        if (
                            submission.package.order.totalQuantity
                            != 1
                        ):
                            raise RuntimeError(
                                "Safety violation: matching "
                                "exit quantity is not 1."
                            )

                        if (
                            submission.package.order.action
                            != "SELL"
                        ):
                            raise RuntimeError(
                                "Safety violation: matching "
                                "exit IB action is not SELL."
                            )

                        if (
                            submission.package.order.transmit
                            is not True
                        ):
                            raise RuntimeError(
                                "Matching paper exit was "
                                "not configured to transmit."
                            )

                        final_record = (
                            wait_for_execution_resolution(
                                execution_ledger=(
                                    execution_ledger
                                ),
                                event_id=(
                                    trade_request.event_id
                                ),
                                kill_switch=(
                                    kill_switch
                                ),
                                timeout_seconds=(
                                    EXECUTION_TIMEOUT_SECONDS
                                ),
                            )
                        )

                        final_execution_status = (
                            final_record.status
                        )

                        if (
                            final_record.status
                            is not ExecutionStatus.FILLED
                        ):
                            raise RuntimeError(
                                "Matching paper exit did not "
                                "reach FILLED. "
                                f"Status: "
                                f"{final_record.status.value}. "
                                f"Reason: "
                                f"{final_record.reason}"
                            )

                        refresh_position_snapshot(
                            app=app,
                            manager=manager,
                            broker_client=broker_client,
                        )

                        final_mbt_position = (
                            get_mbt_position(
                                broker_client
                            )
                        )

                        require_flat_after_exit(
                            broker_client
                        )

                        print()
                        print(
                            "MATCHING EXIT FILLED."
                        )
                        print(
                            "TWS paper account returned "
                            "to flat."
                        )
                        print(
                            "ONE-ORDER LIMIT REACHED."
                        )

                        break

            else:
                raise RuntimeError(
                    "Unsupported Eagle message: "
                    f"{type(message).__name__}"
                )

            print(
                f"Messages observed: "
                f"{messages_observed}/{max_messages}"
            )

            if (
                messages_observed
                >= max_messages
            ):
                break

        if not armed:
            refresh_position_snapshot(
                app=app,
                manager=manager,
                broker_client=broker_client,
            )

            final_mbt_position = (
                get_mbt_position(
                    broker_client
                )
            )

            if final_mbt_position != 1:
                raise RuntimeError(
                    "Observe-only matching-exit test "
                    "unexpectedly changed the MBT position."
                )

            final_lifecycle_state = (
                lifecycle_guard.get_state(
                    TARGET_SIGNAL_ID
                )
            )

            if (
                final_lifecycle_state
                is not SignalLifecycleState.LONG_OPEN
            ):
                raise RuntimeError(
                    "Observe-only matching-exit test "
                    "unexpectedly changed durable lifecycle."
                )

            if (
                event_store.get_last_seq()
                != original_eagle_cursor
            ):
                raise RuntimeError(
                    "Observe-only matching-exit test "
                    "unexpectedly changed Eagle cursor."
                )

        else:
            final_lifecycle_state = (
                lifecycle_guard.get_state(
                    TARGET_SIGNAL_ID
                )
            )

        return MatchingExitResult(
            armed=armed,

            eagle_hello_received=(
                eagle_hello_received
            ),
            eagle_environment_staging=(
                eagle_environment_staging
            ),

            starting_lifecycle_state=(
                starting_lifecycle_state
            ),
            entry_execution_status=(
                entry_record.status
            ),

            initial_position_count=(
                initial_position_count
            ),
            initial_mbt_position=(
                initial_mbt_position
            ),

            target_exit_seen=(
                target_exit_seen
            ),
            target_exit_event_id=(
                target_exit_event_id
            ),
            target_exit_intent=(
                target_exit_intent
            ),

            ignored_entry_count=(
                ignored_entry_count
            ),
            ignored_other_exit_count=(
                ignored_other_exit_count
            ),
            ignored_non_btc_count=(
                ignored_non_btc_count
            ),

            risk_approved=(
                risk_approved
            ),
            readiness_passed=(
                readiness_passed
            ),

            broker_submission_count=(
                broker_submission_count
            ),
            broker_order_id=(
                broker_order_id
            ),

            final_execution_status=(
                final_execution_status
            ),
            final_mbt_position=(
                final_mbt_position
            ),

            final_lifecycle_state=(
                final_lifecycle_state
            ),

            kill_switch_active=(
                kill_switch.active
            ),

            final_eagle_cursor=(
                event_store.get_last_seq()
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
    result: MatchingExitResult,
) -> None:
    """Print the controlled matching-exit result."""

    print()
    print()
    print(
        "REAL EAGLE MATCHING EXIT -> IB PAPER SUMMARY"
    )
    print("=" * 72)

    print(
        f"Mode armed:                  "
        f"{result.armed}"
    )
    print(
        f"Eagle hello received:        "
        f"{result.eagle_hello_received}"
    )
    print(
        f"Eagle staging confirmed:     "
        f"{result.eagle_environment_staging}"
    )
    print(
        f"Starting lifecycle:          "
        f"{result.starting_lifecycle_state}"
    )
    print(
        f"Entry execution status:      "
        f"{result.entry_execution_status.value}"
    )
    print(
        f"Initial position count:      "
        f"{result.initial_position_count}"
    )
    print(
        f"Initial MBT position:        "
        f"{result.initial_mbt_position}"
    )
    print(
        f"Target exit seen:            "
        f"{result.target_exit_seen}"
    )
    print(
        f"Target exit event:           "
        f"{result.target_exit_event_id}"
    )
    print(
        f"Target exit intent:          "
        f"{result.target_exit_intent}"
    )
    print(
        f"Entries ignored:             "
        f"{result.ignored_entry_count}"
    )
    print(
        f"Other exits ignored:         "
        f"{result.ignored_other_exit_count}"
    )
    print(
        f"Non-BTC entries ignored:     "
        f"{result.ignored_non_btc_count}"
    )
    print(
        f"Risk approved:               "
        f"{result.risk_approved}"
    )
    print(
        f"IB readiness passed:         "
        f"{result.readiness_passed}"
    )
    print(
        f"Broker submissions:          "
        f"{result.broker_submission_count}"
    )
    print(
        f"Broker order ID:             "
        f"{result.broker_order_id}"
    )

    final_status = (
        result.final_execution_status.value
        if result.final_execution_status
        is not None
        else None
    )

    print(
        f"Final execution status:      "
        f"{final_status}"
    )
    print(
        f"Final MBT position:          "
        f"{result.final_mbt_position}"
    )
    print(
        f"Final lifecycle state:       "
        f"{result.final_lifecycle_state}"
    )
    print(
        f"Kill switch active:          "
        f"{result.kill_switch_active}"
    )
    print(
        f"Final Eagle cursor:          "
        f"{result.final_eagle_cursor}"
    )

    print("=" * 72)

    if result.successful:
        if result.armed:
            print(
                "RESULT: PASS - the exact matching "
                "Eagle exit closed the 1-MBT TWS "
                "paper position."
            )

            print(
                "PAPER ACCOUNT IS FLAT."
            )

        else:
            print(
                "RESULT: PASS - matching exit was "
                "validated in OBSERVE-ONLY mode."
            )

            print(
                "NO DURABLE STATE WAS MUTATED."
            )
            print(
                "NO BROKER ORDER WAS SUBMITTED."
            )

    else:
        print(
            "RESULT: FAIL - matching-exit "
            "safety validation failed."
        )

    print()


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Close the exact Eagle-generated "
            "1-MBT TWS paper position."
        )
    )

    parser.add_argument(
        ARMING_ARGUMENT,
        action="store_true",
        dest="confirm_paper_exit",
        help=(
            "Explicitly authorize exactly one "
            "matching 1-MBT paper exit."
        ),
    )

    parser.add_argument(
        "--max-messages",
        type=int,
        default=DEFAULT_MAX_MESSAGES,
    )

    parser.add_argument(
        "--event-database",
        default=str(
            DEFAULT_EVENT_DATABASE
        ),
    )

    parser.add_argument(
        "--lifecycle-database",
        default=str(
            DEFAULT_LIFECYCLE_DATABASE
        ),
    )

    parser.add_argument(
        "--execution-ledger",
        default=str(
            DEFAULT_EXECUTION_LEDGER
        ),
    )

    return parser.parse_args()


def main() -> int:
    """Run the matching Eagle exit paper test."""

    arguments = parse_arguments()

    api_key = os.environ.get(
        EAGLE_API_KEY_ENVIRONMENT_VARIABLE
    )

    if not api_key:
        print(
            "BTS_EAGLE_API_KEY is not configured."
        )
        return 1

    armed = bool(
        arguments.confirm_paper_exit
    )

    print()
    print(
        "Starting matching Eagle exit -> "
        "IB PAPER test..."
    )

    print(
        f"Target signal: "
        f"{TARGET_SIGNAL_ID}"
    )

    if armed:
        print(
            "WARNING: ONE MATCHING TWS "
            "PAPER EXIT IS ARMED."
        )

    else:
        print(
            "OBSERVE-ONLY MODE."
        )
        print(
            "No broker exit can be submitted."
        )
        print(
            "No durable Eagle/lifecycle state "
            "will be mutated."
        )

    try:
        result = asyncio.run(
            run_matching_exit_test(
                armed=armed,
                api_key=api_key,
                event_database_path=(
                    arguments.event_database
                ),
                lifecycle_database_path=(
                    arguments.lifecycle_database
                ),
                execution_ledger_path=(
                    arguments.execution_ledger
                ),
                max_messages=(
                    arguments.max_messages
                ),
            )
        )

    except Exception as error:
        print()
        print(
            "RESULT: FAIL"
        )
        print(
            f"{type(error).__name__}: "
            f"{error}"
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