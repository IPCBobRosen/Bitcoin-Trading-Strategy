"""Automatic Eagle reconnect integration safety harness.

This harness exercises a real WebSocket disconnect followed by an
automatic BTS reconnect.

It deliberately contains no broker, IB, TradeCoordinator, TradeRequest,
or order-submission path.

Expected scenario with scripts.fake_eagle_disconnect_server:

    SESSION 1
        connect with no durable cursor
        receive fund.hello
        receive seq 1 lifecycle
        receive seq 2 heartbeat
        receive seq 3 lifecycle
        Eagle deliberately closes socket
        BTS catches ConnectionError
        durable cursor must equal 3

    SESSION 2
        BTS automatically builds a NEW EagleClient
        reconnect using since_seq=3
        receive fund.hello
        receive seq 4 heartbeat
        receive seq 5 lifecycle
        receive fresh seq 6 heartbeat
        replay complete
        reconciliation matched
        heartbeat healthy
        reconnect readiness READY
        PASS

No trading component is permitted in this harness.
"""

import argparse
import asyncio
from dataclasses import dataclass
from pathlib import Path

from app.communications.eagle_client import EagleClient
from app.communications.eagle_heartbeat import EagleHeartbeat
from app.communications.eagle_hello import EagleHello
from app.communications.incoming_event import IncomingLifecycleEvent
from app.connection_health import ConnectionHealth
from app.event_processor import (
    EventProcessStatus,
    EventProcessor,
)
from app.event_store import EventStore
from app.heartbeat_processor import HeartbeatProcessor
from app.reconciliation_manager import ReconciliationManager
from app.reconnect_readiness import ReconnectReadiness
from app.replay_tracker import ReplayTracker


DEFAULT_URI = "ws://localhost:8766"

DEFAULT_DATABASE_PATH = (
    Path("data")
    / "eagle_automatic_reconnect_test.db"
)

EXPECTED_FIRST_SESSION_CURSOR = 3
EXPECTED_FINAL_CURSOR = 6

HEARTBEAT_TIMEOUT_SECONDS = 45


@dataclass(frozen=True, slots=True)
class AutomaticReconnectResult:
    """Immutable result of the automatic reconnect scenario."""

    initial_cursor: int | None

    first_connection_uri: str
    first_hello_received: bool
    first_session_lifecycle_count: int
    first_session_heartbeat_count: int
    first_session_replay_expected: int
    first_session_replay_processed: int
    first_session_replay_complete: bool

    disconnect_detected: bool
    cursor_after_disconnect: int | None

    second_connection_uri: str
    second_hello_received: bool
    second_requested_since_seq: int | None
    second_session_lifecycle_count: int
    second_session_heartbeat_count: int
    second_session_replay_expected: int
    second_session_replay_processed: int
    second_session_replay_complete: bool

    reconciliation_matched: bool
    heartbeat_healthy: bool
    reconnect_ready: bool

    final_cursor: int | None

    @property
    def successful(self) -> bool:
        """Return True only for the expected safe recovery sequence."""

        return (
            self.initial_cursor is None
            and "since_seq=" not in self.first_connection_uri
            and self.first_hello_received
            and self.first_session_lifecycle_count == 2
            and self.first_session_heartbeat_count == 1
            and self.first_session_replay_expected == 3
            and self.first_session_replay_processed == 2
            and not self.first_session_replay_complete
            and self.disconnect_detected
            and (
                self.cursor_after_disconnect
                == EXPECTED_FIRST_SESSION_CURSOR
            )
            and "since_seq=3" in self.second_connection_uri
            and self.second_hello_received
            and self.second_requested_since_seq == 3
            and self.second_session_lifecycle_count == 1
            and self.second_session_heartbeat_count == 2
            and self.second_session_replay_expected == 1
            and self.second_session_replay_processed == 1
            and self.second_session_replay_complete
            and self.reconciliation_matched
            and self.heartbeat_healthy
            and self.reconnect_ready
            and self.final_cursor == EXPECTED_FINAL_CURSOR
        )


@dataclass(slots=True)
class SessionCounts:
    """Mutable counters for one Eagle socket session."""

    hello_count: int = 0
    heartbeat_count: int = 0
    lifecycle_count: int = 0


def build_client(
    *,
    uri: str,
    event_store: EventStore,
) -> EagleClient:
    """Create a new EagleClient from the current durable cursor."""

    return EagleClient(
        uri=uri,
        since_seq=event_store.get_last_seq(),
    )


def process_lifecycle_event(
    *,
    event: IncomingLifecycleEvent,
    event_processor: EventProcessor,
    replay_tracker: ReplayTracker,
) -> EventProcessStatus:
    """Process one lifecycle event using accepted-only replay accounting."""

    replay_was_complete = (
        replay_tracker.replay_complete
    )

    process_result = (
        event_processor.process(
            event
        )
    )

    if (
        not replay_was_complete
        and process_result.status
        is EventProcessStatus.ACCEPTED
    ):
        replay_tracker.record_lifecycle_event(
            event
        )

    return process_result.status


async def run_first_session(
    *,
    client: EagleClient,
    event_store: EventStore,
) -> tuple[
    SessionCounts,
    ReplayTracker,
    bool,
]:
    """Run until the fake Eagle server deliberately disconnects."""

    event_processor = EventProcessor(
        event_store
    )

    heartbeat_processor = (
        HeartbeatProcessor(
            event_store
        )
    )

    replay_tracker = ReplayTracker()

    health = ConnectionHealth(
        heartbeat_timeout_seconds=(
            HEARTBEAT_TIMEOUT_SECONDS
        )
    )

    counts = SessionCounts()

    disconnect_detected = False

    print()
    print("=" * 60)
    print("AUTOMATIC RECONNECT - SESSION 1")
    print("=" * 60)
    print(
        f"Connection URI: "
        f"{client._connection_uri()}"
    )

    try:
        async for message in client.listen():
            if isinstance(
                message,
                EagleHello,
            ):
                counts.hello_count += 1

                replay_tracker.process_hello(
                    message
                )

                print()
                print("fund.hello received.")
                print(
                    f"since_seq: "
                    f"{message.since_seq}"
                )
                print(
                    f"last_seq: "
                    f"{message.last_seq}"
                )
                print(
                    f"replay_count: "
                    f"{message.replay_count}"
                )

                continue

            if isinstance(
                message,
                EagleHeartbeat,
            ):
                counts.heartbeat_count += 1

                heartbeat_processor.process(
                    message
                )

                health.record_heartbeat()

                print()
                print(
                    f"Heartbeat seq "
                    f"{message.seq} accepted."
                )
                print(
                    f"Durable cursor: "
                    f"{event_store.get_last_seq()}"
                )

                continue

            if isinstance(
                message,
                IncomingLifecycleEvent,
            ):
                counts.lifecycle_count += 1

                status = (
                    process_lifecycle_event(
                        event=message,
                        event_processor=event_processor,
                        replay_tracker=(
                            replay_tracker
                        ),
                    )
                )

                print()
                print(
                    f"Lifecycle seq "
                    f"{message.seq}: "
                    f"{status.value}"
                )
                print(
                    f"Replay progress: "
                    f"{replay_tracker.processed_replay_count}"
                    f"/"
                    f"{replay_tracker.expected_replay_count}"
                )
                print(
                    f"Durable cursor: "
                    f"{event_store.get_last_seq()}"
                )

                continue

            raise RuntimeError(
                "Unsupported Eagle message object: "
                f"{type(message).__name__}"
            )

    except ConnectionError as error:
        disconnect_detected = True

        print()
        print("=" * 60)
        print("EAGLE DISCONNECT DETECTED")
        print("=" * 60)
        print(
            f"{type(error).__name__}: "
            f"{error}"
        )
        print(
            f"Durable cursor after disconnect: "
            f"{event_store.get_last_seq()}"
        )
        print(
            f"Replay complete: "
            f"{replay_tracker.replay_complete}"
        )
        print(
            "BTS remains fail-closed."
        )
        print("=" * 60)

    return (
        counts,
        replay_tracker,
        disconnect_detected,
    )


async def run_recovery_session(
    *,
    client: EagleClient,
    event_store: EventStore,
) -> tuple[
    SessionCounts,
    ReplayTracker,
    ReconciliationManager,
    ConnectionHealth,
    int | None,
]:
    """Automatically reconnect and finish Eagle recovery."""

    event_processor = EventProcessor(
        event_store
    )

    heartbeat_processor = (
        HeartbeatProcessor(
            event_store
        )
    )

    replay_tracker = ReplayTracker()

    reconciliation_manager = (
        ReconciliationManager()
    )

    health = ConnectionHealth(
        heartbeat_timeout_seconds=(
            HEARTBEAT_TIMEOUT_SECONDS
        )
    )

    reconnect_readiness = (
        ReconnectReadiness(
            replay_tracker,
            reconciliation_manager,
            health,
        )
    )

    counts = SessionCounts()

    requested_since_seq: int | None = None

    print()
    print("=" * 60)
    print("AUTOMATIC RECONNECT - SESSION 2")
    print("=" * 60)
    print(
        f"Connection URI: "
        f"{client._connection_uri()}"
    )

    async for message in client.listen():
        if isinstance(
            message,
            EagleHello,
        ):
            counts.hello_count += 1

            requested_since_seq = (
                message.since_seq
            )

            replay_tracker.process_hello(
                message
            )

            reconciliation_result = (
                reconciliation_manager.reconcile(
                    eagle_positions=(
                        message.open_positions
                    ),
                    broker_positions=[],
                )
            )

            print()
            print("fund.hello received.")
            print(
                f"since_seq: "
                f"{message.since_seq}"
            )
            print(
                f"last_seq: "
                f"{message.last_seq}"
            )
            print(
                f"replay_count: "
                f"{message.replay_count}"
            )
            print(
                f"Reconciliation: "
                f"{reconciliation_result.status.value}"
            )

        elif isinstance(
            message,
            EagleHeartbeat,
        ):
            counts.heartbeat_count += 1

            heartbeat_processor.process(
                message
            )

            health.record_heartbeat()

            print()
            print(
                f"Heartbeat seq "
                f"{message.seq} accepted."
            )
            print(
                f"Durable cursor: "
                f"{event_store.get_last_seq()}"
            )

        elif isinstance(
            message,
            IncomingLifecycleEvent,
        ):
            counts.lifecycle_count += 1

            status = (
                process_lifecycle_event(
                    event=message,
                    event_processor=event_processor,
                    replay_tracker=(
                        replay_tracker
                    ),
                )
            )

            print()
            print(
                f"Lifecycle seq "
                f"{message.seq}: "
                f"{status.value}"
            )
            print(
                f"Replay progress: "
                f"{replay_tracker.processed_replay_count}"
                f"/"
                f"{replay_tracker.expected_replay_count}"
            )

        else:
            raise RuntimeError(
                "Unsupported Eagle message object: "
                f"{type(message).__name__}"
            )

        readiness = (
            reconnect_readiness.evaluate()
        )

        print(
            f"Reconnect ready: "
            f"{readiness.ready}"
        )

        if (
            replay_tracker.hello_received
            and replay_tracker.replay_complete
            and (
                reconciliation_manager
                .last_result
                .matched
            )
            and health.is_healthy()
            and event_store.get_last_seq()
            == EXPECTED_FINAL_CURSOR
        ):
            print()
            print(
                "All reconnect recovery conditions "
                "are satisfied."
            )

            break

    return (
        counts,
        replay_tracker,
        reconciliation_manager,
        health,
        requested_since_seq,
    )


async def run_automatic_reconnect_test(
    *,
    uri: str,
    database_path: str | Path,
) -> AutomaticReconnectResult:
    """Run the complete real socket-drop/reconnect scenario."""

    event_store = EventStore(
        database_path
    )

    initial_cursor = (
        event_store.get_last_seq()
    )

    if initial_cursor is not None:
        raise RuntimeError(
            "Automatic reconnect test requires "
            "a fresh event database. "
            f"Existing durable cursor: {initial_cursor}."
        )

    first_client = build_client(
        uri=uri,
        event_store=event_store,
    )

    first_connection_uri = (
        first_client._connection_uri()
    )

    (
        first_counts,
        first_replay_tracker,
        disconnect_detected,
    ) = await run_first_session(
        client=first_client,
        event_store=event_store,
    )

    cursor_after_disconnect = (
        event_store.get_last_seq()
    )

    if not disconnect_detected:
        raise RuntimeError(
            "Expected Eagle disconnect was not detected."
        )

    if (
        cursor_after_disconnect
        != EXPECTED_FIRST_SESSION_CURSOR
    ):
        raise RuntimeError(
            "Unexpected durable cursor after "
            "the first connection."
        )

    if first_replay_tracker.replay_complete:
        raise RuntimeError(
            "Safety violation: interrupted replay "
            "was incorrectly marked complete."
        )

    print()
    print(
        "Automatically creating a NEW EagleClient "
        "from the durable cursor..."
    )

    second_client = build_client(
        uri=uri,
        event_store=event_store,
    )

    second_connection_uri = (
        second_client._connection_uri()
    )

    (
        second_counts,
        second_replay_tracker,
        reconciliation_manager,
        health,
        second_requested_since_seq,
    ) = await run_recovery_session(
        client=second_client,
        event_store=event_store,
    )

    reconnect_readiness = (
        ReconnectReadiness(
            second_replay_tracker,
            reconciliation_manager,
            health,
        )
    )

    final_readiness = (
        reconnect_readiness.evaluate()
    )

    result = AutomaticReconnectResult(
        initial_cursor=initial_cursor,

        first_connection_uri=(
            first_connection_uri
        ),
        first_hello_received=(
            first_replay_tracker.hello_received
        ),
        first_session_lifecycle_count=(
            first_counts.lifecycle_count
        ),
        first_session_heartbeat_count=(
            first_counts.heartbeat_count
        ),
        first_session_replay_expected=(
            first_replay_tracker.expected_replay_count
        ),
        first_session_replay_processed=(
            first_replay_tracker.processed_replay_count
        ),
        first_session_replay_complete=(
            first_replay_tracker.replay_complete
        ),

        disconnect_detected=(
            disconnect_detected
        ),
        cursor_after_disconnect=(
            cursor_after_disconnect
        ),

        second_connection_uri=(
            second_connection_uri
        ),
        second_hello_received=(
            second_replay_tracker.hello_received
        ),
        second_requested_since_seq=(
            second_requested_since_seq
        ),
        second_session_lifecycle_count=(
            second_counts.lifecycle_count
        ),
        second_session_heartbeat_count=(
            second_counts.heartbeat_count
        ),
        second_session_replay_expected=(
            second_replay_tracker.expected_replay_count
        ),
        second_session_replay_processed=(
            second_replay_tracker.processed_replay_count
        ),
        second_session_replay_complete=(
            second_replay_tracker.replay_complete
        ),

        reconciliation_matched=(
            reconciliation_manager
            .last_result
            .matched
        ),
        heartbeat_healthy=(
            health.is_healthy()
        ),
        reconnect_ready=(
            final_readiness.ready
        ),

        final_cursor=(
            event_store.get_last_seq()
        ),
    )

    return result


def print_result(
    result: AutomaticReconnectResult,
) -> None:
    """Print automatic reconnect test summary."""

    if not isinstance(
        result,
        AutomaticReconnectResult,
    ):
        raise TypeError(
            "'result' must be an "
            "AutomaticReconnectResult."
        )

    print()
    print(
        "BTS / EAGLE AUTOMATIC RECONNECT TEST"
    )
    print("=" * 60)

    print(
        f"Initial cursor:                 "
        f"{result.initial_cursor}"
    )

    print(
        f"First connection URI:           "
        f"{result.first_connection_uri}"
    )

    print(
        f"First hello received:           "
        f"{result.first_hello_received}"
    )

    print(
        f"First replay expected:          "
        f"{result.first_session_replay_expected}"
    )

    print(
        f"First replay processed:         "
        f"{result.first_session_replay_processed}"
    )

    print(
        f"First replay complete:          "
        f"{result.first_session_replay_complete}"
    )

    print(
        f"Disconnect detected:            "
        f"{result.disconnect_detected}"
    )

    print(
        f"Cursor after disconnect:        "
        f"{result.cursor_after_disconnect}"
    )

    print(
        f"Second connection URI:          "
        f"{result.second_connection_uri}"
    )

    print(
        f"Second requested since_seq:     "
        f"{result.second_requested_since_seq}"
    )

    print(
        f"Second replay expected:         "
        f"{result.second_session_replay_expected}"
    )

    print(
        f"Second replay processed:        "
        f"{result.second_session_replay_processed}"
    )

    print(
        f"Second replay complete:         "
        f"{result.second_session_replay_complete}"
    )

    print(
        f"Reconciliation matched:         "
        f"{result.reconciliation_matched}"
    )

    print(
        f"Heartbeat healthy:              "
        f"{result.heartbeat_healthy}"
    )

    print(
        f"Reconnect ready:                "
        f"{result.reconnect_ready}"
    )

    print(
        f"Final durable cursor:           "
        f"{result.final_cursor}"
    )

    print("=" * 60)

    if result.successful:
        print(
            "RESULT: PASS - BTS automatically recovered "
            "from a real Eagle WebSocket disconnect using "
            "the durable replay cursor."
        )

    else:
        print(
            "RESULT: FAIL - automatic Eagle reconnect "
            "safety validation failed."
        )

    print(
        "NO BROKER OR ORDER-SUBMISSION PATH EXISTS."
    )

    print()


def parse_arguments() -> argparse.Namespace:
    """Parse automatic reconnect harness arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run the BTS automatic Eagle reconnect "
            "integration safety harness."
        )
    )

    parser.add_argument(
        "--uri",
        default=DEFAULT_URI,
        help=(
            "Fake Eagle disconnect server URI. "
            f"Default: {DEFAULT_URI}"
        ),
    )

    parser.add_argument(
        "--database",
        default=str(
            DEFAULT_DATABASE_PATH
        ),
        help=(
            "Fresh SQLite database used for "
            "automatic reconnect state."
        ),
    )

    return parser.parse_args()


async def async_main(
    arguments: argparse.Namespace,
) -> AutomaticReconnectResult:
    """Run automatic reconnect scenario from CLI."""

    print()
    print(
        "Starting BTS automatic Eagle reconnect test..."
    )

    print(
        f"Eagle URI: {arguments.uri}"
    )

    print(
        f"Database: {arguments.database}"
    )

    print(
        "NO BROKER OR ORDER-SUBMISSION PATH EXISTS."
    )

    result = (
        await run_automatic_reconnect_test(
            uri=arguments.uri,
            database_path=(
                arguments.database
            ),
        )
    )

    print_result(
        result
    )

    return result


def main() -> int:
    """Run command-line automatic reconnect harness."""

    arguments = parse_arguments()

    try:
        result = asyncio.run(
            async_main(
                arguments
            )
        )

    except KeyboardInterrupt:
        print()
        print(
            "Automatic reconnect test stopped "
            "by operator."
        )

        return 130

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

    return (
        0
        if result.successful
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(
        main()
    )