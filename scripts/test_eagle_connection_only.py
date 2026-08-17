"""Connection-only Eagle integration safety harness.

This harness validates the Eagle communications path without importing or
creating any broker, IB, TradeCoordinator, TradeRequest, or order-submission
component.

It is intentionally incapable of placing an order.
"""

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from app.communications.eagle_client import EagleClient
from app.communications.eagle_heartbeat import EagleHeartbeat
from app.communications.eagle_hello import EagleHello
from app.communications.incoming_event import IncomingLifecycleEvent
from app.connection_health import ConnectionHealth
from app.event_processor import EventProcessStatus, EventProcessor
from app.event_store import EventStore
from app.heartbeat_processor import HeartbeatProcessor
from app.replay_tracker import ReplayTracker


DEFAULT_DATABASE_PATH = (
    Path("data")
    / "eagle_connection_only_test.db"
)

DEFAULT_HEARTBEAT_TIMEOUT_SECONDS = 45
DEFAULT_MAX_MESSAGES = 20


@dataclass(frozen=True, slots=True)
class ConnectionOnlyResult:
    """Summary of one connection-only Eagle session."""

    hello_count: int
    heartbeat_count: int
    lifecycle_count: int
    accepted_lifecycle_count: int
    duplicate_lifecycle_count: int
    out_of_sequence_count: int
    replay_expected: int
    replay_processed: int
    replay_complete: bool
    last_durable_seq: int | None


def build_client(
    *,
    uri: str,
    api_key: str | None,
    event_store: EventStore,
) -> EagleClient:
    """Create an Eagle client using the durable BTS cursor."""

    last_durable_seq = (
        event_store.get_last_seq()
    )

    return EagleClient(
        uri=uri,
        api_key=api_key,
        since_seq=last_durable_seq,
    )


async def run_connection_only_session(
    *,
    client: EagleClient,
    event_store: EventStore,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    heartbeat_timeout_seconds: int = (
        DEFAULT_HEARTBEAT_TIMEOUT_SECONDS
    ),
) -> ConnectionOnlyResult:
    """Receive Eagle messages without any trading or broker path."""

    if (
        not isinstance(max_messages, int)
        or isinstance(max_messages, bool)
        or max_messages <= 0
    ):
        raise ValueError(
            "'max_messages' must be a positive integer."
        )

    event_processor = EventProcessor(
        event_store
    )

    heartbeat_processor = HeartbeatProcessor(
        event_store
    )

    replay_tracker = ReplayTracker()

    connection_health = ConnectionHealth(
        heartbeat_timeout_seconds=(
            heartbeat_timeout_seconds
        )
    )

    hello_count = 0
    heartbeat_count = 0
    lifecycle_count = 0
    accepted_lifecycle_count = 0
    duplicate_lifecycle_count = 0
    out_of_sequence_count = 0

    print()
    print(
        "BTS / EAGLE CONNECTION-ONLY SAFETY TEST"
    )
    print("=" * 60)
    print(
        "NO BROKER OR ORDER-SUBMISSION PATH EXISTS."
    )
    print(
        "NO TradeCoordinator or TradeRequest path exists."
    )
    print(
        f"Reconnect URI: {client._connection_uri()}"
    )
    print(
        f"Durable cursor before connection: "
        f"{event_store.get_last_seq()}"
    )
    print("=" * 60)
    print()

    async for message in client.listen():
        if isinstance(
            message,
            EagleHello,
        ):
            hello_count += 1

            replay_tracker.process_hello(
                message
            )

            print(
                f"fund.hello #{hello_count} received."
            )
            print(
                f"Server last_seq: {message.last_seq}"
            )
            print(
                f"Requested since_seq: "
                f"{message.since_seq}"
            )
            print(
                f"Replay count: {message.replay_count}"
            )
            print(
                f"Open count: {message.open_count}"
            )
            print(
                f"Environment: "
                f"{message.environment.value}"
            )

        elif isinstance(
            message,
            EagleHeartbeat,
        ):
            heartbeat_count += 1

            heartbeat_processor.process(
                message
            )

            connection_health.record_heartbeat()

            print(
                f"fund.heartbeat "
                f"#{heartbeat_count} received."
            )
            print(
                f"Heartbeat seq: {message.seq}"
            )
            print(
                f"Heartbeat healthy: "
                f"{connection_health.is_healthy()}"
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

            replay_was_complete = (
                replay_tracker.replay_complete
            )

            process_result = (
                event_processor.process(
                    message
                )
            )

            if (
                process_result.status
                is EventProcessStatus.ACCEPTED
            ):
                accepted_lifecycle_count += 1

            elif (
                process_result.status
                is EventProcessStatus.DUPLICATE_EVENT
            ):
                duplicate_lifecycle_count += 1

            elif (
                process_result.status
                is EventProcessStatus.OUT_OF_SEQUENCE
            ):
                out_of_sequence_count += 1

            else:
                raise RuntimeError(
                    "Unsupported EventProcessStatus: "
                    f"{process_result.status!r}"
                )

            if (
                not replay_was_complete
                and process_result.status
                is EventProcessStatus.ACCEPTED
            ):
                replay_tracker.record_lifecycle_event(
                    message
                )

            print(
                f"Lifecycle #{lifecycle_count} observed."
            )
            print(
                f"Type: {message.message_type}"
            )
            print(
                f"Event ID: {message.event_id}"
            )
            print(
                f"Signal ID: {message.signal_id}"
            )
            print(
                f"Seq: {message.seq}"
            )
            print(
                f"Processing status: "
                f"{process_result.status.value}"
            )
            print(
                "Lifecycle event stopped before "
                "any trading component."
            )

        else:
            raise RuntimeError(
                "Unsupported Eagle message object: "
                f"{type(message).__name__}"
            )

        total_messages = (
            hello_count
            + heartbeat_count
            + lifecycle_count
        )

        print(
            f"Messages observed: "
            f"{total_messages}/{max_messages}"
        )
        print("-" * 60)

        if total_messages >= max_messages:
            break

    result = ConnectionOnlyResult(
        hello_count=hello_count,
        heartbeat_count=heartbeat_count,
        lifecycle_count=lifecycle_count,
        accepted_lifecycle_count=(
            accepted_lifecycle_count
        ),
        duplicate_lifecycle_count=(
            duplicate_lifecycle_count
        ),
        out_of_sequence_count=(
            out_of_sequence_count
        ),
        replay_expected=(
            replay_tracker.expected_replay_count
        ),
        replay_processed=(
            replay_tracker.processed_replay_count
        ),
        replay_complete=(
            replay_tracker.replay_complete
        ),
        last_durable_seq=(
            event_store.get_last_seq()
        ),
    )

    print()
    print(
        "BTS / EAGLE CONNECTION-ONLY TEST SUMMARY"
    )
    print("=" * 60)
    print(
        f"Hello frames:               "
        f"{result.hello_count}"
    )
    print(
        f"Heartbeats:                 "
        f"{result.heartbeat_count}"
    )
    print(
        f"Lifecycle events:           "
        f"{result.lifecycle_count}"
    )
    print(
        f"Accepted lifecycle events:  "
        f"{result.accepted_lifecycle_count}"
    )
    print(
        f"Duplicate lifecycle events: "
        f"{result.duplicate_lifecycle_count}"
    )
    print(
        f"Out-of-sequence events:     "
        f"{result.out_of_sequence_count}"
    )
    print(
        f"Replay expected:            "
        f"{result.replay_expected}"
    )
    print(
        f"Replay processed:           "
        f"{result.replay_processed}"
    )
    print(
        f"Replay complete:            "
        f"{result.replay_complete}"
    )
    print(
        f"Final durable cursor:       "
        f"{result.last_durable_seq}"
    )
    print("=" * 60)
    print(
        "NO BROKER CALLS WERE POSSIBLE."
    )
    print(
        "NO ORDERS WERE POSSIBLE."
    )

    return result


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run the BTS Eagle connection-only "
            "integration safety harness."
        )
    )

    parser.add_argument(
        "--uri",
        default=os.environ.get(
            "BTS_EAGLE_URI",
            "ws://localhost:8765",
        ),
        help=(
            "Eagle WebSocket URI. "
            "Defaults to BTS_EAGLE_URI or "
            "ws://localhost:8765."
        ),
    )

    parser.add_argument(
        "--api-key",
        default=os.environ.get(
            "BTS_EAGLE_API_KEY"
        ),
        help=(
            "Eagle API key. Prefer the "
            "BTS_EAGLE_API_KEY environment variable "
            "so secrets are not stored in shell history."
        ),
    )

    parser.add_argument(
        "--database",
        default=str(
            DEFAULT_DATABASE_PATH
        ),
        help=(
            "SQLite database used for the "
            "connection-only durable cursor."
        ),
    )

    parser.add_argument(
        "--max-messages",
        type=int,
        default=DEFAULT_MAX_MESSAGES,
        help=(
            "Stop after observing this many "
            "validated Eagle messages."
        ),
    )

    return parser.parse_args()


async def async_main(
    arguments: argparse.Namespace,
) -> ConnectionOnlyResult:
    """Create dependencies and run the safety harness."""

    event_store = EventStore(
        arguments.database
    )

    client = build_client(
        uri=arguments.uri,
        api_key=arguments.api_key,
        event_store=event_store,
    )

    print()
    print(
        "Starting Eagle connection-only harness..."
    )
    print(
        f"Eagle URI: {arguments.uri}"
    )
    print(
        f"API key configured: "
        f"{client.has_api_key}"
    )
    print(
        f"Database: {arguments.database}"
    )
    print(
        f"Maximum messages: "
        f"{arguments.max_messages}"
    )

    return await run_connection_only_session(
        client=client,
        event_store=event_store,
        max_messages=arguments.max_messages,
    )


def main() -> None:
    """Run the command-line connection-only harness."""

    arguments = parse_arguments()

    try:
        asyncio.run(
            async_main(
                arguments
            )
        )

    except KeyboardInterrupt:
        print()
        print(
            "Connection-only harness stopped "
            "by operator."
        )

    except ConnectionRefusedError:
        print()
        print(
            "Connection refused."
        )
        print(
            "For the offline test, start the "
            "fake Eagle server first."
        )

    except ConnectionError as error:
        print()
        print(
            f"Eagle connection error: {error}"
        )

    except ValueError as error:
        print()
        print(
            f"Eagle validation error: {error}"
        )


if __name__ == "__main__":
    main()