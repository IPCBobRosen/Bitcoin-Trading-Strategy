"""Controlled real-Eagle STAGING to IB paper bridge.

This harness joins the already-tested Eagle decision path to the
already-tested Interactive Brokers paper execution path.

Safety model
============

Eagle side:

    real Eagle STAGING WebSocket
        ->
    IncomingLifecycleEvent
        ->
    durable EventProcessor
        ->
    BTC-only EagleTradeAdapter
        ->
    production TradeCoordinator
        ->
    TradeRequest

Broker side:

    TradeRequest
        ->
    actual paper-account position
        ->
    RiskManager
        ->
    IBTradingReadiness
        ->
    IBExecutionClient
        ->
    TWS PAPER

Critical safeguards
===================

1. Eagle must report env=staging.
2. ETHUSDT and every non-BTC instrument are ignored.
3. Historical Eagle replay is NEVER eligible for broker submission.
4. A post-replay Eagle heartbeat is required before submission.
5. Eagle must report no currently-open Fund signals at handshake.
6. The TWS paper account must be completely flat.
7. The durable execution ledger must start empty.
8. Only MBT is permitted.
9. Quantity is exactly 1.
10. Maximum absolute broker position is 1.
11. RiskManager approval is mandatory.
12. IBTradingReadiness approval is mandatory.
13. Exactly one broker submission is permitted per run.
14. Broker execution is disabled unless the explicit
    --confirm-paper-order argument is supplied.
15. In normal/default mode, this harness OBSERVES ONLY and never calls
    IBExecutionClient.submit().

Keep TWS Read-Only API enabled during development and observe-only
testing. Disable Read-Only only for the final explicitly armed paper
order test.
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
from app.communications.protocol import TradeIntent
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
from app.signal_lifecycle_guard import SignalLifecycleGuard
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


# ---------------------------------------------------------------------
# Real Eagle STAGING
# ---------------------------------------------------------------------

EAGLE_URI = (
    "wss://tracer.eagleailabs.com/"
    "ipc-api/ipc/v1/fund/stream"
)

EAGLE_API_KEY_ENVIRONMENT_VARIABLE = (
    "BTS_EAGLE_API_KEY"
)


# ---------------------------------------------------------------------
# Interactive Brokers PAPER
# ---------------------------------------------------------------------

IB_HOST = "127.0.0.1"
IB_PORT = 7497
IB_CLIENT_ID = 1

SYMBOL = "MBT"
EXPECTED_LOCAL_SYMBOL = "MBTQ6"
EXCHANGE = "CME"
CURRENCY = "USD"
TRADING_CLASS = "MBT"

# Same contract currently used by the proven paper-order harness.
CONTRACT_MONTH = "20260828"

PAPER_QUANTITY = 1
STOP_LOSS_POINTS = Decimal("500")

MAX_DAILY_LOSS = Decimal("1000")
MAX_ORDER_QUANTITY = 1
MAX_ABSOLUTE_POSITION = 1


# ---------------------------------------------------------------------
# Timing / persistence
# ---------------------------------------------------------------------

CONNECTION_TIMEOUT_SECONDS = 10.0
EXECUTION_TIMEOUT_SECONDS = 20.0

DEFAULT_MAX_MESSAGES = 100

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


# Explicit order-transmission gate.
ARMING_ARGUMENT = "--confirm-paper-order"


@dataclass(frozen=True, slots=True)
class RealEagleToIBBridgeResult:
    """Immutable result of one Eagle-to-IB bridge session."""

    armed: bool

    eagle_hello_received: bool
    eagle_environment_staging: bool
    eagle_open_count: int | None

    initial_broker_position_count: int
    initial_mbt_position: int

    replay_expected: int
    replay_processed: int
    replay_complete: bool
    post_replay_heartbeat_seen: bool

    btc_events_adapted: int
    non_btc_entries_ignored: int
    unknown_exits_ignored: int

    approved_trade_decisions: int
    rejected_trade_decisions: int

    live_eligible_trade_requests: int

    broker_submission_count: int

    submitted_event_id: str | None
    submitted_intent: str | None
    broker_order_id: int | None

    risk_approved: bool
    readiness_passed: bool

    final_execution_status: ExecutionStatus | None
    final_mbt_position: int

    kill_switch_active: bool

    final_eagle_cursor: int | None

    @property
    def successful(self) -> bool:
        """Return True when the requested bridge mode succeeded."""

        common_success = (
            self.eagle_hello_received
            and self.eagle_environment_staging
            and self.eagle_open_count == 0
            and self.initial_broker_position_count == 0
            and self.initial_mbt_position == 0
            and self.replay_complete
            and self.post_replay_heartbeat_seen
            and not self.kill_switch_active
        )

        if not common_success:
            return False

        if not self.armed:
            return (
                self.broker_submission_count == 0
                and self.submitted_event_id is None
                and self.broker_order_id is None
            )

        return (
            self.broker_submission_count == 1
            and self.submitted_event_id is not None
            and self.submitted_intent is not None
            and self.broker_order_id is not None
            and self.risk_approved
            and self.readiness_passed
            and self.final_execution_status
            is ExecutionStatus.FILLED
            and abs(
                self.final_mbt_position
            )
            == 1
        )


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
        not isinstance(
            description,
            str,
        )
        or not description.strip()
    ):
        raise ValueError(
            "'description' must be a non-empty string."
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
    """Wait for the paper order to reach a terminal BTS state."""

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
            "Eagle-to-IB paper execution resolution"
        ),
        timeout_seconds=timeout_seconds,
    )

    if kill_switch.active:
        raise RuntimeError(
            "BTS kill switch activated while waiting "
            "for paper execution: "
            f"{kill_switch.reason}"
        )

    record = execution_ledger.get(
        event_id
    )

    if record is None:
        raise RuntimeError(
            "Paper execution disappeared from "
            "the durable execution ledger."
        )

    return record


def get_mbt_position(
    broker_client: IBBrokerClient,
) -> int:
    """Return current signed MBT position from broker state."""

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
    """Require the complete TWS paper account to be flat."""

    positions = (
        broker_client.get_raw_positions()
    )

    if positions:
        raise RuntimeError(
            "Eagle-to-IB bridge requires the entire "
            "TWS paper account to start flat."
        )

    if (
        get_mbt_position(
            broker_client
        )
        != 0
    ):
        raise RuntimeError(
            "Eagle-to-IB bridge requires MBT "
            "position to start at zero."
        )


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


def build_decision_components(
    *,
    lifecycle_database_path: str | Path,
    trading_controls: TradingControls,
) -> tuple[
    SignalLifecycleGuard,
    EagleTradeAdapter,
    TradeCoordinator,
]:
    """Build Eagle adapter and production BTS decision components."""

    lifecycle_guard = (
        SignalLifecycleGuard(
            lifecycle_database_path
        )
    )

    adapter = EagleTradeAdapter(
        lifecycle_guard
    )

    coordinator = TradeCoordinator(
        controls=trading_controls,
        signal_lifecycle_guard=(
            lifecycle_guard
        ),
    )

    return (
        lifecycle_guard,
        adapter,
        coordinator,
    )


def validate_live_trade_request(
    trade_request,
) -> None:
    """Enforce the hard-coded first bridge execution policy."""

    if trade_request.environment.value != "staging":
        raise RuntimeError(
            "Bridge permits Eagle STAGING "
            "TradeRequests only."
        )

    if trade_request.symbol != SYMBOL:
        raise RuntimeError(
            "Bridge permits MBT only."
        )

    if (
        trade_request.quantity
        != PAPER_QUANTITY
    ):
        raise RuntimeError(
            "Bridge permits exactly 1 MBT."
        )

    if trade_request.intent not in {
        TradeIntent.BUY_TO_OPEN,
        TradeIntent.SELL_TO_OPEN,
    }:
        raise RuntimeError(
            "First bridge submission permits "
            "opening intents only."
        )


async def run_bridge(
    *,
    armed: bool,
    eagle_uri: str,
    api_key: str,
    event_database_path: str | Path,
    lifecycle_database_path: str | Path,
    execution_ledger_path: str | Path,
    max_messages: int,
) -> RealEagleToIBBridgeResult:
    """Run the controlled Eagle STAGING to TWS-paper bridge."""

    if not isinstance(
        armed,
        bool,
    ):
        raise TypeError(
            "'armed' must be a bool."
        )

    if (
        not isinstance(
            max_messages,
            int,
        )
        or isinstance(
            max_messages,
            bool,
        )
        or max_messages <= 0
    ):
        raise ValueError(
            "'max_messages' must be a positive integer."
        )

    # ---------------------------------------------------------
    # Durable state
    # ---------------------------------------------------------

    event_store = EventStore(
        event_database_path
    )

    event_processor = EventProcessor(
        event_store
    )

    heartbeat_processor = (
        HeartbeatProcessor(
            event_store
        )
    )

    execution_ledger = (
        ExecutionLedger(
            execution_ledger_path
        )
    )

    if execution_ledger.all_records():
        raise RuntimeError(
            "Bridge execution ledger must be empty "
            "before a controlled run."
        )

    # ---------------------------------------------------------
    # Shared risk / control state
    # ---------------------------------------------------------

    kill_switch = KillSwitch()

    trading_controls = TradingControls(
        symbol=SYMBOL,
        quantity=PAPER_QUANTITY,
        stop_loss_points=(
            STOP_LOSS_POINTS
        ),
    )

    daily_loss_guard = (
        DailyLossGuard(
            MAX_DAILY_LOSS
        )
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

    (
        lifecycle_guard,
        adapter,
        coordinator,
    ) = build_decision_components(
        lifecycle_database_path=(
            lifecycle_database_path
        ),
        trading_controls=(
            trading_controls
        ),
    )

    # TradeCoordinator needs controls enabled so replay can
    # establish durable signal lifecycle state.
    #
    # Broker execution remains separately gated by replay state,
    # heartbeat state, risk, readiness, and explicit arming.
    trading_controls.resume()

    # ---------------------------------------------------------
    # TWS paper connection
    # ---------------------------------------------------------

    broker_client = IBBrokerClient()

    duplicate_guard = (
        DuplicateOrderGuard()
    )

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

    initial_position_count = 0
    initial_mbt_position = 0
    final_mbt_position = 0

    eagle_hello_received = False
    eagle_environment_staging = False
    eagle_open_count: int | None = None

    replay_expected = 0
    replay_processed = 0
    replay_complete = False
    post_replay_heartbeat_seen = False

    btc_events_adapted = 0
    non_btc_entries_ignored = 0
    unknown_exits_ignored = 0

    approved_trade_decisions = 0
    rejected_trade_decisions = 0

    live_eligible_trade_requests = 0

    broker_submission_count = 0

    submitted_event_id: str | None = None
    submitted_intent: str | None = None
    broker_order_id: int | None = None

    risk_approved = False
    readiness_passed = False

    final_execution_status: (
        ExecutionStatus | None
    ) = None

    messages_observed = 0

    try:
        # -----------------------------------------------------
        # Establish paper broker state first.
        # -----------------------------------------------------

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
                "IB connection handshake: "
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

        require_completely_flat(
            broker_client
        )

        print()
        print(
            "TWS PAPER PRE-FLIGHT"
        )
        print("=" * 72)
        print(
            f"API ready:                "
            f"{app.api_ready.ready}"
        )
        print(
            f"Next valid order ID:      "
            f"{app.api_ready.next_valid_order_id}"
        )
        print(
            f"Position snapshot:        "
            f"{broker_client.snapshot_complete}"
        )
        print(
            f"Open positions:           "
            f"{initial_position_count}"
        )
        print(
            f"Initial MBT position:     "
            f"{initial_mbt_position}"
        )
        print(
            f"Kill switch active:       "
            f"{kill_switch.active}"
        )
        print(
            f"Broker submission armed:  "
            f"{armed}"
        )
        print("=" * 72)

        # -----------------------------------------------------
        # Eagle client starts from durable cursor.
        # -----------------------------------------------------

        eagle_client = EagleClient(
            uri=eagle_uri,
            api_key=api_key,
            since_seq=(
                event_store.get_last_seq()
            ),
        )

        print()
        print(
            "REAL EAGLE -> IB PAPER BRIDGE"
        )
        print("=" * 72)
        print(
            f"Eagle URI: {eagle_client._connection_uri()}"
        )
        print(
            "Environment required: STAGING"
        )
        print(
            "Historical replay submission: BLOCKED"
        )
        print(
            "Post-replay heartbeat required: YES"
        )
        print(
            "Maximum broker submissions: 1"
        )

        if armed:
            print(
                "MODE: ARMED FOR ONE PAPER ORDER"
            )
        else:
            print(
                "MODE: OBSERVE ONLY - NO ORDER CAN BE SENT"
            )

        print("=" * 72)

        async for message in (
            eagle_client.listen()
        ):
            messages_observed += 1

            print()
            print("-" * 72)

            # -------------------------------------------------
            # fund.hello
            # -------------------------------------------------

            if isinstance(
                message,
                EagleHello,
            ):
                eagle_hello_received = True

                eagle_environment_staging = (
                    message.environment.value
                    == "staging"
                )

                eagle_open_count = (
                    message.open_count
                )

                replay_expected = (
                    message.replay_count
                )

                replay_processed = 0

                replay_complete = (
                    replay_expected == 0
                )

                post_replay_heartbeat_seen = (
                    False
                )

                print(
                    "fund.hello received."
                )
                print(
                    f"Environment:        "
                    f"{message.environment.value}"
                )
                print(
                    f"Replay expected:    "
                    f"{replay_expected}"
                )
                print(
                    f"Eagle open count:   "
                    f"{message.open_count}"
                )
                print(
                    f"Server last_seq:    "
                    f"{message.last_seq}"
                )
                print(
                    f"Requested since_seq:"
                    f" {message.since_seq}"
                )

                if not eagle_environment_staging:
                    raise RuntimeError(
                        "Safety violation: bridge connected "
                        "to non-STAGING Eagle environment."
                    )

                if (
                    message.open_count
                    != 0
                ):
                    raise RuntimeError(
                        "Safety violation: first bridge "
                        "requires Eagle open_count=0."
                    )

            # -------------------------------------------------
            # fund.heartbeat
            # -------------------------------------------------

            elif isinstance(
                message,
                EagleHeartbeat,
            ):
                heartbeat_processor.process(
                    message
                )

                if replay_complete:
                    post_replay_heartbeat_seen = (
                        True
                    )

                print(
                    f"fund.heartbeat seq "
                    f"{message.seq}"
                )
                print(
                    f"Replay complete: "
                    f"{replay_complete}"
                )
                print(
                    "Post-replay heartbeat: "
                    f"{post_replay_heartbeat_seen}"
                )
                print(
                    f"Durable cursor: "
                    f"{event_store.get_last_seq()}"
                )

            # -------------------------------------------------
            # Eagle lifecycle event
            # -------------------------------------------------

            elif isinstance(
                message,
                IncomingLifecycleEvent,
            ):
                was_replay_event = (
                    not replay_complete
                )

                event_result = (
                    event_processor.process(
                        message
                    )
                )

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
                print(
                    "Event status: "
                    f"{event_result.status.value}"
                )

                if (
                    event_result.status
                    is EventProcessStatus.ACCEPTED
                ):
                    if was_replay_event:
                        replay_processed += 1

                        if (
                            replay_processed
                            >= replay_expected
                        ):
                            replay_complete = True

                            print(
                                "Historical Eagle replay "
                                "is now complete."
                            )

                    if message.message_type not in {
                        "fund.entry",
                        "fund.exit",
                    }:
                        print(
                            "Lifecycle type ignored by "
                            "the current trade adapter."
                        )

                    else:
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
                            is EagleTradeAdaptStatus.IGNORED_SYMBOL
                        ):
                            non_btc_entries_ignored += 1

                            print(
                                "Non-BTC instrument ignored."
                            )

                        elif (
                            adapt_result.status
                            is EagleTradeAdaptStatus.IGNORED_UNKNOWN_EXIT
                        ):
                            unknown_exits_ignored += 1

                            print(
                                "Exit ignored because no "
                                "open BTC lifecycle exists."
                            )

                        elif (
                            adapt_result.status
                            is EagleTradeAdaptStatus.ADAPTED
                        ):
                            btc_events_adapted += 1

                            normalized_event = (
                                adapt_result.event
                            )

                            if normalized_event is None:
                                raise RuntimeError(
                                    "Adapted Eagle event "
                                    "contained no event."
                                )

                            print(
                                "Normalized intent: "
                                f"{normalized_event.payload['intent']}"
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

                            if decision.approved:
                                approved_trade_decisions += 1
                            else:
                                rejected_trade_decisions += 1

                            if (
                                decision.approved
                                and decision.trade_request
                                is not None
                            ):
                                trade_request = (
                                    decision.trade_request
                                )

                                # -----------------------------
                                # Replay can NEVER submit.
                                # -----------------------------

                                if was_replay_event:
                                    print(
                                        "REPLAY HARD STOP - "
                                        "historical signal cannot "
                                        "reach broker submission."
                                    )

                                # -----------------------------
                                # First bridge submits only a
                                # LIVE opening event.
                                # -----------------------------

                                elif (
                                    trade_request.intent
                                    not in {
                                        TradeIntent.BUY_TO_OPEN,
                                        TradeIntent.SELL_TO_OPEN,
                                    }
                                ):
                                    print(
                                        "LIVE CLOSE observed but "
                                        "first bridge permits "
                                        "opening submissions only."
                                    )

                                elif (
                                    not post_replay_heartbeat_seen
                                ):
                                    print(
                                        "LIVE HARD STOP - no "
                                        "post-replay heartbeat yet."
                                    )

                                else:
                                    live_eligible_trade_requests += 1

                                    print(
                                        "LIVE BTC TradeRequest "
                                        "reached paper bridge gate."
                                    )
                                    print(
                                        f"Intent:   "
                                        f"{trade_request.intent.value}"
                                    )
                                    print(
                                        f"Symbol:   "
                                        f"{trade_request.symbol}"
                                    )
                                    print(
                                        f"Quantity: "
                                        f"{trade_request.quantity}"
                                    )

                                    validate_live_trade_request(
                                        trade_request
                                    )

                                    # -------------------------
                                    # Default mode stops here.
                                    # -------------------------

                                    if not armed:
                                        print(
                                            "OBSERVE-ONLY HARD STOP."
                                        )
                                        print(
                                            "IBExecutionClient.submit "
                                            "was NOT called."
                                        )

                                    else:
                                        if (
                                            broker_submission_count
                                            >= 1
                                        ):
                                            raise RuntimeError(
                                                "Safety violation: "
                                                "one-order maximum "
                                                "already reached."
                                            )

                                        # ---------------------
                                        # Refresh actual broker
                                        # state immediately
                                        # before submission.
                                        # ---------------------

                                        refresh_position_snapshot(
                                            app=app,
                                            manager=manager,
                                            broker_client=broker_client,
                                        )

                                        current_position = (
                                            get_mbt_position(
                                                broker_client
                                            )
                                        )

                                        require_completely_flat(
                                            broker_client
                                        )

                                        # ---------------------
                                        # RiskManager
                                        # ---------------------

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
                                            "Risk approved: "
                                            f"{risk_approved}"
                                        )
                                        print(
                                            "Projected position: "
                                            f"{risk_decision.projected_position}"
                                        )

                                        if not risk_decision.approved:
                                            raise RuntimeError(
                                                "RiskManager rejected "
                                                "the live Eagle "
                                                "TradeRequest: "
                                                f"{risk_decision.reason}"
                                            )

                                        # ---------------------
                                        # Reconciliation
                                        #
                                        # First bridge requires:
                                        # Eagle open_count = 0
                                        # before live signal,
                                        # broker position = 0.
                                        # ---------------------

                                        positions_reconciled = (
                                            eagle_open_count == 0
                                            and current_position == 0
                                        )

                                        execution_state_clear = (
                                            len(
                                                execution_ledger.all_records()
                                            )
                                            == 0
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

                                        # ---------------------
                                        # Construct final IB
                                        # execution components
                                        # only after all gates.
                                        # ---------------------

                                        order_factory = (
                                            IBOrderFactory(
                                                exchange=EXCHANGE,
                                                currency=CURRENCY,
                                                trading_class=(
                                                    TRADING_CLASS
                                                ),
                                                order_type="MKT",
                                                time_in_force="DAY",
                                                transmit=True,
                                            )
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
                                            "ONE PAPER ORDER "
                                            "SUBMISSION AUTHORIZED"
                                        )
                                        print(
                                            "=" * 72
                                        )
                                        print(
                                            f"Eagle event: "
                                            f"{trade_request.event_id}"
                                        )
                                        print(
                                            f"Intent: "
                                            f"{trade_request.intent.value}"
                                        )
                                        print(
                                            "Quantity: 1 MBT"
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

                                        submitted_event_id = (
                                            trade_request.event_id
                                        )

                                        submitted_intent = (
                                            trade_request.intent.value
                                        )

                                        if (
                                            submission.package.order.totalQuantity
                                            != 1
                                        ):
                                            raise RuntimeError(
                                                "Safety violation: "
                                                "generated order "
                                                "quantity is not 1."
                                            )

                                        expected_action = (
                                            "BUY"
                                            if (
                                                trade_request.intent
                                                is TradeIntent.BUY_TO_OPEN
                                            )
                                            else "SELL"
                                        )

                                        if (
                                            submission.package.order.action
                                            != expected_action
                                        ):
                                            raise RuntimeError(
                                                "Safety violation: "
                                                "IB action does not "
                                                "match Eagle intent."
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
                                                "Paper order did not "
                                                "reach FILLED. "
                                                "Final status: "
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

                                        expected_position = (
                                            1
                                            if (
                                                trade_request.intent
                                                is TradeIntent.BUY_TO_OPEN
                                            )
                                            else -1
                                        )

                                        if (
                                            final_mbt_position
                                            != expected_position
                                        ):
                                            raise RuntimeError(
                                                "Filled paper order "
                                                "did not create the "
                                                "expected MBT position. "
                                                f"Expected "
                                                f"{expected_position}, "
                                                f"observed "
                                                f"{final_mbt_position}."
                                            )

                                        print()
                                        print(
                                            "ONE-ORDER LIMIT REACHED."
                                        )
                                        print(
                                            "Bridge is stopping now."
                                        )

                                        break

                else:
                    print(
                        "Duplicate/out-of-sequence event "
                        "stopped before decision processing."
                    )

            else:
                raise RuntimeError(
                    "Unsupported Eagle message: "
                    f"{type(message).__name__}"
                )

            print(
                f"Messages observed: "
                f"{messages_observed}/{max_messages}"
            )
            print(
                f"Replay progress: "
                f"{replay_processed}/{replay_expected}"
            )

            if (
                messages_observed
                >= max_messages
            ):
                break

        # -----------------------------------------------------
        # Final broker snapshot for observe-only mode.
        # -----------------------------------------------------

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

            if final_mbt_position != 0:
                raise RuntimeError(
                    "Observe-only bridge unexpectedly "
                    "changed the MBT broker position."
                )

        return RealEagleToIBBridgeResult(
            armed=armed,

            eagle_hello_received=(
                eagle_hello_received
            ),
            eagle_environment_staging=(
                eagle_environment_staging
            ),
            eagle_open_count=(
                eagle_open_count
            ),

            initial_broker_position_count=(
                initial_position_count
            ),
            initial_mbt_position=(
                initial_mbt_position
            ),

            replay_expected=(
                replay_expected
            ),
            replay_processed=(
                replay_processed
            ),
            replay_complete=(
                replay_complete
            ),
            post_replay_heartbeat_seen=(
                post_replay_heartbeat_seen
            ),

            btc_events_adapted=(
                btc_events_adapted
            ),
            non_btc_entries_ignored=(
                non_btc_entries_ignored
            ),
            unknown_exits_ignored=(
                unknown_exits_ignored
            ),

            approved_trade_decisions=(
                approved_trade_decisions
            ),
            rejected_trade_decisions=(
                rejected_trade_decisions
            ),

            live_eligible_trade_requests=(
                live_eligible_trade_requests
            ),

            broker_submission_count=(
                broker_submission_count
            ),

            submitted_event_id=(
                submitted_event_id
            ),
            submitted_intent=(
                submitted_intent
            ),
            broker_order_id=(
                broker_order_id
            ),

            risk_approved=(
                risk_approved
            ),
            readiness_passed=(
                readiness_passed
            ),

            final_execution_status=(
                final_execution_status
            ),
            final_mbt_position=(
                final_mbt_position
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
    result: RealEagleToIBBridgeResult,
) -> None:
    """Print the bridge result."""

    print()
    print()
    print(
        "REAL EAGLE -> IB PAPER BRIDGE SUMMARY"
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
        f"Eagle open count:            "
        f"{result.eagle_open_count}"
    )

    print(
        f"Initial paper positions:     "
        f"{result.initial_broker_position_count}"
    )

    print(
        f"Initial MBT position:        "
        f"{result.initial_mbt_position}"
    )

    print(
        f"Replay expected:             "
        f"{result.replay_expected}"
    )

    print(
        f"Replay processed:            "
        f"{result.replay_processed}"
    )

    print(
        f"Replay complete:             "
        f"{result.replay_complete}"
    )

    print(
        f"Post-replay heartbeat:       "
        f"{result.post_replay_heartbeat_seen}"
    )

    print(
        f"BTC events adapted:          "
        f"{result.btc_events_adapted}"
    )

    print(
        f"Non-BTC entries ignored:     "
        f"{result.non_btc_entries_ignored}"
    )

    print(
        f"Unknown exits ignored:       "
        f"{result.unknown_exits_ignored}"
    )

    print(
        f"Approved decisions:          "
        f"{result.approved_trade_decisions}"
    )

    print(
        f"Rejected decisions:          "
        f"{result.rejected_trade_decisions}"
    )

    print(
        f"Live eligible requests:      "
        f"{result.live_eligible_trade_requests}"
    )

    print(
        f"Broker submissions:          "
        f"{result.broker_submission_count}"
    )

    print(
        f"Submitted event:             "
        f"{result.submitted_event_id}"
    )

    print(
        f"Submitted intent:            "
        f"{result.submitted_intent}"
    )

    print(
        f"Broker order ID:             "
        f"{result.broker_order_id}"
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
        "Final execution status:      "
        f"{(
            result.final_execution_status.value
            if result.final_execution_status
            is not None
            else None
        )}"
    )

    print(
        f"Final MBT position:          "
        f"{result.final_mbt_position}"
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
                "RESULT: PASS - exactly one real Eagle "
                "STAGING signal completed through the "
                "TWS paper execution path."
            )

            print(
                "IMPORTANT: The resulting 1-MBT PAPER "
                "position remains OPEN."
            )

        else:
            print(
                "RESULT: PASS - Eagle and TWS paper bridge "
                "completed in OBSERVE-ONLY mode."
            )

            print(
                "NO BROKER ORDER WAS SUBMITTED."
            )

    else:
        print(
            "RESULT: FAIL - bridge safety validation failed."
        )

    print()


def parse_arguments() -> argparse.Namespace:
    """Parse bridge command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Controlled real Eagle STAGING to "
            "Interactive Brokers PAPER bridge."
        )
    )

    parser.add_argument(
        ARMING_ARGUMENT,
        action="store_true",
        dest="confirm_paper_order",
        help=(
            "Explicitly authorize at most one "
            "1-MBT TWS paper order."
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
    """Run the controlled real Eagle to IB paper bridge."""

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
        arguments.confirm_paper_order
    )

    print()
    print(
        "Starting real Eagle -> IB PAPER bridge..."
    )

    if armed:
        print(
            "WARNING: ONE TWS PAPER ORDER IS ARMED."
        )
    else:
        print(
            "OBSERVE-ONLY MODE."
        )
        print(
            "IBExecutionClient.submit will not be called."
        )

    try:
        result = asyncio.run(
            run_bridge(
                armed=armed,
                eagle_uri=EAGLE_URI,
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