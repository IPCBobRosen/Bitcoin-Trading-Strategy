"""Real Eagle STAGING decision-only integration safety harness.

This harness connects to the real Eagle STAGING Fund lane and exercises:

    Eagle WebSocket
        ->
    IncomingLifecycleEvent
        ->
    durable EventProcessor
        ->
    BTC-only EagleTradeAdapter
        ->
    production TradeCoordinator
        ->
    TradeDecision / TradeRequest
        ->
    HARD STOP

BTCUSDT entries are translated into BTS trade intent.

BTCUSDT exits are translated using durable signal lifecycle state.

ETHUSDT and all other instruments are ignored before TradeCoordinator.

fund.update messages are observed and durably sequenced, but intentionally
do not enter TradeCoordinator in this version.

NO Interactive Brokers client is imported.
NO TWS connection is created.
NO broker client is created.
NO order factory is created.
NO order-submission function exists in this harness.
"""

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from app.communications.eagle_client import EagleClient
from app.communications.eagle_heartbeat import EagleHeartbeat
from app.communications.eagle_hello import EagleHello
from app.communications.eagle_trade_adapter import (
    EagleTradeAdaptStatus,
    EagleTradeAdapter,
)
from app.communications.incoming_event import IncomingLifecycleEvent
from app.event_processor import (
    EventProcessStatus,
    EventProcessor,
)
from app.event_store import EventStore
from app.heartbeat_processor import HeartbeatProcessor
from app.signal_lifecycle_guard import (
    SignalLifecycleGuard,
    SignalLifecycleState,
)
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


DEFAULT_URI = (
    "wss://tracer.eagleailabs.com/"
    "ipc-api/ipc/v1/fund/stream"
)

DEFAULT_EVENT_DATABASE = (
    Path("data")
    / "real_eagle_staging_decision_events.db"
)

DEFAULT_LIFECYCLE_DATABASE = (
    Path("data")
    / "real_eagle_staging_decision_signals.db"
)

DEFAULT_MAX_MESSAGES = 40

SUPPORTED_DECISION_MESSAGE_TYPES = {
    "fund.entry",
    "fund.exit",
}


@dataclass(frozen=True, slots=True)
class RealEagleStagingDecisionResult:
    """Immutable summary of one real-Eagle decision-only session."""

    hello_count: int
    heartbeat_count: int
    lifecycle_count: int

    accepted_event_count: int
    duplicate_event_count: int
    out_of_sequence_count: int

    btc_adapted_count: int
    ignored_symbol_count: int
    ignored_unknown_exit_count: int
    ignored_other_lifecycle_count: int

    approved_decision_count: int
    rejected_decision_count: int
    trade_request_count: int

    long_open_count: int
    short_open_count: int
    closed_signal_count: int

    final_event_cursor: int | None

    broker_calls_possible: bool
    order_submission_possible: bool

    @property
    def successful(self) -> bool:
        """Return True when the decision-only safety boundary held."""

        return (
            self.broker_calls_possible is False
            and self.order_submission_possible is False
            and (
                self.trade_request_count
                == self.approved_decision_count
            )
        )


def build_client(
    *,
    uri: str,
    api_key: str,
    event_store: EventStore,
) -> EagleClient:
    """Build a real Eagle client from current durable sequence state."""

    return EagleClient(
        uri=uri,
        api_key=api_key,
        since_seq=event_store.get_last_seq(),
    )


def build_decision_components(
    *,
    lifecycle_database_path: str | Path,
) -> tuple[
    SignalLifecycleGuard,
    EagleTradeAdapter,
    TradeCoordinator,
]:
    """Create the BTC-only adapter and production decision layer."""

    lifecycle_guard = SignalLifecycleGuard(
        lifecycle_database_path
    )

    adapter = EagleTradeAdapter(
        lifecycle_guard
    )

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    # This only permits TradeRequest construction.
    #
    # There is no execution path in this harness.
    controls.resume()

    coordinator = TradeCoordinator(
        controls=controls,
        signal_lifecycle_guard=lifecycle_guard,
    )

    return (
        lifecycle_guard,
        adapter,
        coordinator,
    )


def print_hard_stop() -> None:
    """Print the non-execution boundary."""

    print(
        "HARD STOP - nothing sent to a broker or execution client."
    )


async def run_real_eagle_staging_decision_test(
    *,
    uri: str,
    api_key: str,
    event_database_path: str | Path,
    lifecycle_database_path: str | Path,
    max_messages: int,
) -> RealEagleStagingDecisionResult:
    """Run one real Eagle STAGING decision-only session."""

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

    (
        lifecycle_guard,
        adapter,
        coordinator,
    ) = build_decision_components(
        lifecycle_database_path=(
            lifecycle_database_path
        )
    )

    client = build_client(
        uri=uri,
        api_key=api_key,
        event_store=event_store,
    )

    print()
    print(
        "BTS / REAL EAGLE STAGING DECISION-ONLY TEST"
    )
    print("=" * 72)
    print(
        "NO INTERACTIVE BROKERS / TWS CONNECTION EXISTS."
    )
    print(
        "NO BROKER OR ORDER-SUBMISSION PATH EXISTS."
    )
    print(
        "BTCUSDT may reach TradeCoordinator."
    )
    print(
        "ETHUSDT and unsupported instruments are ignored."
    )
    print(
        "TradeDecision / TradeRequest is the HARD STOP."
    )
    print(
        f"Reconnect URI: {client._connection_uri()}"
    )
    print(
        f"Durable cursor before connection: "
        f"{event_store.get_last_seq()}"
    )
    print("=" * 72)

    hello_count = 0
    heartbeat_count = 0
    lifecycle_count = 0

    accepted_event_count = 0
    duplicate_event_count = 0
    out_of_sequence_count = 0

    btc_adapted_count = 0
    ignored_symbol_count = 0
    ignored_unknown_exit_count = 0
    ignored_other_lifecycle_count = 0

    approved_decision_count = 0
    rejected_decision_count = 0
    trade_request_count = 0

    messages_observed = 0

    async for message in client.listen():
        messages_observed += 1

        print()
        print("-" * 72)

        if isinstance(
            message,
            EagleHello,
        ):
            hello_count += 1

            print(
                f"fund.hello #{hello_count}"
            )
            print(
                f"Environment:         "
                f"{message.environment.value}"
            )
            print(
                f"Requested since_seq: "
                f"{message.since_seq}"
            )
            print(
                f"Server last_seq:      "
                f"{message.last_seq}"
            )
            print(
                f"Replay count:         "
                f"{message.replay_count}"
            )
            print(
                f"Open count:           "
                f"{message.open_count}"
            )

            if (
                message.environment.value
                != "staging"
            ):
                raise RuntimeError(
                    "Safety violation: real-Eagle decision-only "
                    "harness connected to a non-staging environment."
                )

        elif isinstance(
            message,
            EagleHeartbeat,
        ):
            heartbeat_count += 1

            heartbeat_processor.process(
                message
            )

            print(
                f"fund.heartbeat #{heartbeat_count}"
            )
            print(
                f"Seq:            {message.seq}"
            )
            print(
                f"Durable cursor: "
                f"{event_store.get_last_seq()}"
            )

        elif isinstance(
            message,
            IncomingLifecycleEvent,
        ):
            lifecycle_count += 1

            print(
                f"Lifecycle #{lifecycle_count}"
            )
            print(
                f"Type:      {message.message_type}"
            )
            print(
                f"Seq:       {message.seq}"
            )
            print(
                f"Event ID:  {message.event_id}"
            )
            print(
                f"Signal ID: {message.signal_id}"
            )

            event_result = (
                event_processor.process(
                    message
                )
            )

            print(
                "Event processing status: "
                f"{event_result.status.value}"
            )

            if (
                event_result.status
                is EventProcessStatus.DUPLICATE_EVENT
            ):
                duplicate_event_count += 1

                print(
                    "Duplicate stopped before adapter."
                )

                print_hard_stop()

            elif (
                event_result.status
                is EventProcessStatus.OUT_OF_SEQUENCE
            ):
                out_of_sequence_count += 1

                print(
                    "Out-of-sequence event stopped "
                    "before adapter."
                )

                print_hard_stop()

            elif (
                event_result.status
                is EventProcessStatus.ACCEPTED
            ):
                accepted_event_count += 1

                if (
                    message.message_type
                    not in SUPPORTED_DECISION_MESSAGE_TYPES
                ):
                    ignored_other_lifecycle_count += 1

                    print(
                        "Lifecycle type intentionally ignored "
                        "by current decision adapter."
                    )

                    print_hard_stop()

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

                    print(
                        "Eagle symbol: "
                        f"{adapt_result.eagle_symbol}"
                    )

                    if (
                        adapt_result.status
                        is EagleTradeAdaptStatus.IGNORED_SYMBOL
                    ):
                        ignored_symbol_count += 1

                        print(
                            "Non-BTC instrument ignored."
                        )

                        print_hard_stop()

                    elif (
                        adapt_result.status
                        is EagleTradeAdaptStatus.IGNORED_UNKNOWN_EXIT
                    ):
                        ignored_unknown_exit_count += 1

                        print(
                            "Exit ignored because no open "
                            "BTC lifecycle exists."
                        )

                        print_hard_stop()

                    elif (
                        adapt_result.status
                        is EagleTradeAdaptStatus.ADAPTED
                    ):
                        btc_adapted_count += 1

                        normalized_event = (
                            adapt_result.event
                        )

                        if normalized_event is None:
                            raise RuntimeError(
                                "Adapted Eagle event unexpectedly "
                                "contained no normalized event."
                            )

                        print(
                            "Normalized BTS intent: "
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

                        print(
                            "Trade decision reason:   "
                            f"{decision.reason}"
                        )

                        if decision.approved:
                            approved_decision_count += 1

                            request = (
                                decision.trade_request
                            )

                            if request is None:
                                raise RuntimeError(
                                    "Approved TradeDecision contained "
                                    "no TradeRequest."
                                )

                            trade_request_count += 1

                            print(
                                "APPROVED TradeRequest:"
                            )
                            print(
                                f"  Intent:   "
                                f"{request.intent.value}"
                            )
                            print(
                                f"  Symbol:   "
                                f"{request.symbol}"
                            )
                            print(
                                f"  Quantity: "
                                f"{request.quantity}"
                            )
                            print(
                                f"  Signal:   "
                                f"{request.signal_id}"
                            )

                        else:
                            rejected_decision_count += 1

                        current_state = (
                            lifecycle_guard.get_state(
                                normalized_event.signal_id
                            )
                        )

                        print(
                            "Durable signal state: "
                            f"{current_state}"
                        )

                        print_hard_stop()

                    else:
                        raise RuntimeError(
                            "Unsupported EagleTradeAdaptStatus: "
                            f"{adapt_result.status!r}"
                        )

            else:
                raise RuntimeError(
                    "Unsupported EventProcessStatus: "
                    f"{event_result.status!r}"
                )

        else:
            raise RuntimeError(
                "Unsupported Eagle message object: "
                f"{type(message).__name__}"
            )

        print(
            f"Messages observed: "
            f"{messages_observed}/{max_messages}"
        )

        if messages_observed >= max_messages:
            break

    snapshots = (
        lifecycle_guard.all_snapshots()
    )

    long_open_count = sum(
        1
        for snapshot in snapshots
        if (
            snapshot.state
            is SignalLifecycleState.LONG_OPEN
        )
    )

    short_open_count = sum(
        1
        for snapshot in snapshots
        if (
            snapshot.state
            is SignalLifecycleState.SHORT_OPEN
        )
    )

    closed_signal_count = sum(
        1
        for snapshot in snapshots
        if (
            snapshot.state
            is SignalLifecycleState.CLOSED
        )
    )

    return RealEagleStagingDecisionResult(
        hello_count=hello_count,
        heartbeat_count=heartbeat_count,
        lifecycle_count=lifecycle_count,

        accepted_event_count=(
            accepted_event_count
        ),
        duplicate_event_count=(
            duplicate_event_count
        ),
        out_of_sequence_count=(
            out_of_sequence_count
        ),

        btc_adapted_count=(
            btc_adapted_count
        ),
        ignored_symbol_count=(
            ignored_symbol_count
        ),
        ignored_unknown_exit_count=(
            ignored_unknown_exit_count
        ),
        ignored_other_lifecycle_count=(
            ignored_other_lifecycle_count
        ),

        approved_decision_count=(
            approved_decision_count
        ),
        rejected_decision_count=(
            rejected_decision_count
        ),
        trade_request_count=(
            trade_request_count
        ),

        long_open_count=(
            long_open_count
        ),
        short_open_count=(
            short_open_count
        ),
        closed_signal_count=(
            closed_signal_count
        ),

        final_event_cursor=(
            event_store.get_last_seq()
        ),

        broker_calls_possible=False,
        order_submission_possible=False,
    )


def print_result(
    result: RealEagleStagingDecisionResult,
) -> None:
    """Print final real-Eagle decision-only summary."""

    print()
    print()
    print(
        "REAL EAGLE STAGING DECISION-ONLY SUMMARY"
    )
    print("=" * 72)

    print(
        f"Hello frames:                 "
        f"{result.hello_count}"
    )
    print(
        f"Heartbeats:                   "
        f"{result.heartbeat_count}"
    )
    print(
        f"Lifecycle events:             "
        f"{result.lifecycle_count}"
    )
    print(
        f"Accepted Eagle events:        "
        f"{result.accepted_event_count}"
    )
    print(
        f"Duplicate Eagle events:       "
        f"{result.duplicate_event_count}"
    )
    print(
        f"Out-of-sequence events:       "
        f"{result.out_of_sequence_count}"
    )

    print(
        f"BTC events adapted:           "
        f"{result.btc_adapted_count}"
    )
    print(
        f"Non-BTC entries ignored:      "
        f"{result.ignored_symbol_count}"
    )
    print(
        f"Unknown exits ignored:        "
        f"{result.ignored_unknown_exit_count}"
    )
    print(
        f"Other lifecycle ignored:      "
        f"{result.ignored_other_lifecycle_count}"
    )

    print(
        f"Approved trade decisions:     "
        f"{result.approved_decision_count}"
    )
    print(
        f"Rejected trade decisions:     "
        f"{result.rejected_decision_count}"
    )
    print(
        f"TradeRequests constructed:    "
        f"{result.trade_request_count}"
    )

    print(
        f"LONG_OPEN signals:            "
        f"{result.long_open_count}"
    )
    print(
        f"SHORT_OPEN signals:           "
        f"{result.short_open_count}"
    )
    print(
        f"CLOSED signals:               "
        f"{result.closed_signal_count}"
    )

    print(
        f"Final durable cursor:         "
        f"{result.final_event_cursor}"
    )

    print("=" * 72)

    print(
        "NO INTERACTIVE BROKERS / TWS CONNECTION EXISTED."
    )
    print(
        "NO BROKER CALLS WERE POSSIBLE."
    )
    print(
        "NO ORDERS WERE POSSIBLE."
    )

    if result.successful:
        print()
        print(
            "RESULT: PASS - real Eagle staging traffic "
            "reached the BTS decision layer and stopped "
            "before execution."
        )

    else:
        print()
        print(
            "RESULT: FAIL - decision-only safety "
            "boundary validation failed."
        )


def parse_arguments() -> argparse.Namespace:
    """Parse CLI options."""

    parser = argparse.ArgumentParser(
        description=(
            "Run real Eagle STAGING through the BTS "
            "BTC decision layer without execution."
        )
    )

    parser.add_argument(
        "--uri",
        default=DEFAULT_URI,
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
        "--max-messages",
        type=int,
        default=DEFAULT_MAX_MESSAGES,
    )

    return parser.parse_args()


def main() -> int:
    """Run the real Eagle STAGING decision-only harness."""

    arguments = parse_arguments()

    api_key = os.environ.get(
        "BTS_EAGLE_API_KEY"
    )

    if not api_key:
        print(
            "BTS_EAGLE_API_KEY is not configured."
        )
        return 1

    print()
    print(
        "Starting real Eagle STAGING decision-only test..."
    )
    print(
        "TWS SHOULD REMAIN CLOSED FOR THIS TEST."
    )

    try:
        result = asyncio.run(
            run_real_eagle_staging_decision_test(
                uri=arguments.uri,
                api_key=api_key,
                event_database_path=(
                    arguments.event_database
                ),
                lifecycle_database_path=(
                    arguments.lifecycle_database
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