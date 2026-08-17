"""Eagle lifecycle-to-decision integration safety harness.

This harness connects to Eagle through the production EagleClient,
durably processes lifecycle-event identity and sequence state, and
passes accepted lifecycle events through the production
TradeCoordinator.

The harness intentionally stops at TradeDecision / TradeRequest.

No broker client, execution client, order factory, risk execution
pipeline, or order-submission function is imported or created.
"""

import argparse
import asyncio
import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from app.communications.eagle_client import EagleClient
from app.communications.eagle_heartbeat import EagleHeartbeat
from app.communications.eagle_hello import EagleHello
from app.communications.incoming_event import IncomingLifecycleEvent
from app.event_processor import (
    EventProcessStatus,
    EventProcessor,
)
from app.event_store import EventStore
from app.heartbeat_processor import HeartbeatProcessor
from app.signal_lifecycle_guard import SignalLifecycleGuard
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


DEFAULT_URI = "ws://localhost:8765"
DEFAULT_EVENT_DATABASE = (
    "data/eagle_lifecycle_decision_events.db"
)
DEFAULT_LIFECYCLE_DATABASE = (
    "data/eagle_lifecycle_decision_signals.db"
)
DEFAULT_MAX_MESSAGES = 20

DEFAULT_SYMBOL = "MBT"
DEFAULT_QUANTITY = 1
DEFAULT_STOP_LOSS_POINTS = Decimal("500")
DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 45


@dataclass(frozen=True, slots=True)
class LifecycleDecisionResult:
    """Immutable summary of one decision-only Eagle session."""

    hello_count: int
    heartbeat_count: int
    lifecycle_count: int

    accepted_event_count: int
    duplicate_event_count: int
    out_of_sequence_count: int

    approved_decision_count: int
    rejected_decision_count: int

    trade_request_count: int

    final_event_cursor: int | None
    durable_signal_count: int

    broker_calls_possible: bool
    order_submission_possible: bool

    @property
    def successful(self) -> bool:
        """Return True when the harness preserved its safety boundary."""

        return (
            self.broker_calls_possible is False
            and self.order_submission_possible is False
            and self.trade_request_count
            == self.approved_decision_count
        )


def build_client(
    *,
    uri: str,
    api_key: str | None,
    event_store: EventStore,
) -> EagleClient:
    """Create Eagle client from the current durable cursor."""

    if not isinstance(
        event_store,
        EventStore,
    ):
        raise TypeError(
            "'event_store' must be an EventStore."
        )

    return EagleClient(
        uri=uri,
        api_key=api_key,
        since_seq=event_store.get_last_seq(),
    )


def build_trade_coordinator(
    *,
    lifecycle_database_path: str | Path,
) -> TradeCoordinator:
    """Create the production decision layer for this safe harness."""

    controls = TradingControls(
        symbol=DEFAULT_SYMBOL,
        quantity=DEFAULT_QUANTITY,
        stop_loss_points=(
            DEFAULT_STOP_LOSS_POINTS
        ),
    )

    # TradingControls intentionally starts paused.
    #
    # We resume it only so the production TradeCoordinator can
    # construct and evaluate TradeRequest objects. There is no broker
    # or order-submission component anywhere in this harness.
    controls.resume()

    lifecycle_guard = SignalLifecycleGuard(
        lifecycle_database_path
    )

    return TradeCoordinator(
        controls=controls,
        signal_lifecycle_guard=lifecycle_guard,
    )


def print_safety_header(
    *,
    client: EagleClient,
    event_database_path: Path,
    lifecycle_database_path: Path,
) -> None:
    """Print the explicit Module 3 safety boundary."""

    print()
    print(
        "BTS / EAGLE LIFECYCLE DECISION-ONLY SAFETY TEST"
    )
    print("=" * 68)

    print(
        "REAL Eagle lifecycle messages may reach TradeCoordinator."
    )

    print(
        "TradeRequest objects may be constructed and inspected."
    )

    print(
        "NO BROKER OR ORDER-SUBMISSION PATH EXISTS."
    )

    print(
        "NO EXECUTION CLIENT OR ORDER FACTORY EXISTS."
    )

    print(
        "TradeDecision / TradeRequest is the HARD STOP."
    )

    print(
        f"Connection URI: {client._connection_uri()}"
    )

    print(
        f"Event database: {event_database_path}"
    )

    print(
        f"Lifecycle database: {lifecycle_database_path}"
    )

    print("=" * 68)
    print()


def print_trade_request(
    event: IncomingLifecycleEvent,
    decision,
) -> None:
    """Print the approved request without executing anything."""

    request = decision.trade_request

    if request is None:
        raise RuntimeError(
            "Approved TradeDecision did not contain "
            "a TradeRequest."
        )

    print(
        "APPROVED TradeDecision reached HARD STOP."
    )

    print(
        f"Event ID:          {request.event_id}"
    )

    print(
        f"Signal ID:         {request.signal_id}"
    )

    print(
        f"Eagle type:        {event.message_type}"
    )

    print(
        f"Intent:            {request.intent.value}"
    )

    print(
        f"Symbol:            {request.symbol}"
    )

    print(
        f"Quantity:          {request.quantity}"
    )

    print(
        "Stop-loss points:  "
        f"{request.stop_loss_points}"
    )

    print(
        f"Decision reason:   {decision.reason}"
    )

    print()
    print(
        "HARD STOP: TradeRequest was NOT sent "
        "to any execution component."
    )


async def run_lifecycle_decision_only_test(
    *,
    uri: str,
    api_key: str | None,
    event_database_path: str | Path,
    lifecycle_database_path: str | Path,
    max_messages: int,
) -> LifecycleDecisionResult:
    """Run one Eagle lifecycle-to-decision safety session."""

    if (
        not isinstance(max_messages, int)
        or isinstance(max_messages, bool)
        or max_messages <= 0
    ):
        raise ValueError(
            "'max_messages' must be a positive integer."
        )

    event_database = Path(
        event_database_path
    )

    lifecycle_database = Path(
        lifecycle_database_path
    )

    event_store = EventStore(
        event_database
    )

    event_processor = EventProcessor(
        event_store
    )

    coordinator = build_trade_coordinator(
        lifecycle_database_path=(
            lifecycle_database
        ),
    )

    heartbeat_processor = HeartbeatProcessor(
        event_store
    )

    client = build_client(
        uri=uri,
        api_key=api_key,
        event_store=event_store,
    )

    print_safety_header(
        client=client,
        event_database_path=event_database,
        lifecycle_database_path=(
            lifecycle_database
        ),
    )

    hello_count = 0
    heartbeat_count = 0
    lifecycle_count = 0

    accepted_event_count = 0
    duplicate_event_count = 0
    out_of_sequence_count = 0

    approved_decision_count = 0
    rejected_decision_count = 0
    trade_request_count = 0

    messages_observed = 0

    async for message in client.listen():
        messages_observed += 1

        if isinstance(
            message,
            EagleHello,
        ):
            hello_count += 1

            print(
                f"fund.hello #{hello_count} received."
            )

            print(
                f"Requested since_seq: {message.since_seq}"
            )

            print(
                f"Server last_seq:      {message.last_seq}"
            )

            print(
                f"Replay count:         {message.replay_count}"
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
                f"fund.heartbeat #{heartbeat_count} received."
            )

            print(
                f"Heartbeat seq:        {message.seq}"
            )

            print(
                "Heartbeat persisted:  True"
            )

            print(
                "Durable event cursor: "
                f"{event_store.get_last_seq()}"
            )

        elif isinstance(
            message,
            IncomingLifecycleEvent,
        ):
            lifecycle_count += 1

            print(
                f"Lifecycle #{lifecycle_count} received."
            )

            print(
                f"Type:      {message.message_type}"
            )

            print(
                f"Event ID:  {message.event_id}"
            )

            print(
                f"Signal ID: {message.signal_id}"
            )

            print(
                f"Seq:       {message.seq}"
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
                is EventProcessStatus.ACCEPTED
            ):
                accepted_event_count += 1

                decision = (
                    coordinator.process_event(
                        message
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

                    if decision.trade_request is None:
                        raise RuntimeError(
                            "Approved TradeDecision did not "
                            "contain a TradeRequest."
                        )

                    trade_request_count += 1

                    print_trade_request(
                        message,
                        decision,
                    )

                else:
                    rejected_decision_count += 1

                    print()
                    print(
                        "REJECTED TradeDecision."
                    )

                    print(
                        "No TradeRequest continued beyond "
                        "the decision boundary."
                    )

            elif (
                event_result.status
                is EventProcessStatus.DUPLICATE_EVENT
            ):
                duplicate_event_count += 1

                print()
                print(
                    "Duplicate lifecycle event stopped "
                    "before TradeCoordinator."
                )

            elif (
                event_result.status
                is EventProcessStatus.OUT_OF_SEQUENCE
            ):
                out_of_sequence_count += 1

                print()
                print(
                    "Out-of-sequence lifecycle event stopped "
                    "before TradeCoordinator."
                )

            else:
                raise RuntimeError(
                    "Unsupported EventProcessStatus: "
                    f"{event_result.status!r}"
                )

        else:
            raise RuntimeError(
                "Unsupported validated Eagle message: "
                f"{type(message)!r}"
            )

        print(
            f"Messages observed: "
            f"{messages_observed}/{max_messages}"
        )

        print("-" * 68)

        if messages_observed >= max_messages:
            break

    lifecycle_guard = (
        coordinator.signal_lifecycle_guard
    )

    durable_signal_count = len(
        lifecycle_guard.all_snapshots()
    )

    return LifecycleDecisionResult(
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

        approved_decision_count=(
            approved_decision_count
        ),

        rejected_decision_count=(
            rejected_decision_count
        ),

        trade_request_count=(
            trade_request_count
        ),

        final_event_cursor=(
            event_store.get_last_seq()
        ),

        durable_signal_count=(
            durable_signal_count
        ),

        broker_calls_possible=False,
        order_submission_possible=False,
    )


def print_result(
    result: LifecycleDecisionResult,
) -> None:
    """Print the final decision-only integration summary."""

    if not isinstance(
        result,
        LifecycleDecisionResult,
    ):
        raise TypeError(
            "'result' must be a "
            "LifecycleDecisionResult."
        )

    print()
    print(
        "BTS / EAGLE LIFECYCLE DECISION-ONLY TEST SUMMARY"
    )
    print("=" * 68)

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
        f"Out-of-sequence Eagle events: "
        f"{result.out_of_sequence_count}"
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
        f"Durable signal lifecycles:    "
        f"{result.durable_signal_count}"
    )

    print(
        f"Final durable event cursor:   "
        f"{result.final_event_cursor}"
    )

    print("=" * 68)

    print(
        "NO BROKER CALLS WERE POSSIBLE."
    )

    print(
        "NO ORDERS WERE POSSIBLE."
    )

    if result.successful:
        print()
        print(
            "RESULT: PASS - Eagle lifecycle events reached "
            "the BTS decision layer and stopped safely "
            "before execution."
        )

    else:
        print()
        print(
            "RESULT: FAIL - Decision-only safety boundary "
            "validation failed."
        )


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run the BTS Eagle lifecycle decision-only "
            "integration safety harness."
        )
    )

    parser.add_argument(
        "--uri",
        default=DEFAULT_URI,
        help=(
            "Eagle WebSocket URI. "
            f"Default: {DEFAULT_URI}"
        ),
    )

    parser.add_argument(
        "--event-database",
        default=DEFAULT_EVENT_DATABASE,
        help=(
            "SQLite database for durable Eagle event state."
        ),
    )

    parser.add_argument(
        "--lifecycle-database",
        default=DEFAULT_LIFECYCLE_DATABASE,
        help=(
            "SQLite database for durable signal lifecycle state."
        ),
    )

    parser.add_argument(
        "--max-messages",
        type=int,
        default=DEFAULT_MAX_MESSAGES,
        help=(
            "Maximum number of Eagle messages to observe."
        ),
    )

    return parser.parse_args()


def main() -> int:
    """Run the lifecycle decision-only integration harness."""

    arguments = parse_arguments()

    api_key = os.environ.get(
        "BTS_EAGLE_API_KEY"
    )

    print()
    print(
        "Starting Eagle lifecycle decision-only harness..."
    )

    print(
        f"Eagle URI: {arguments.uri}"
    )

    print(
        "API key configured: "
        f"{bool(api_key)}"
    )

    print(
        f"Event database: "
        f"{arguments.event_database}"
    )

    print(
        f"Lifecycle database: "
        f"{arguments.lifecycle_database}"
    )

    print(
        f"Maximum messages: "
        f"{arguments.max_messages}"
    )

    print()
    print(
        "NO BROKER OR ORDER-SUBMISSION PATH EXISTS."
    )

    try:
        result = asyncio.run(
            run_lifecycle_decision_only_test(
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

    except ConnectionError as error:
        print()
        print(
            f"Eagle connection error: {error}"
        )
        return 1

    except (TypeError, ValueError, RuntimeError) as error:
        print()
        print(
            f"Lifecycle decision test error: {error}"
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