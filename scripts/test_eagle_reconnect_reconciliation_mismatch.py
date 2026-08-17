"""Eagle reconnect reconciliation-mismatch safety harness.

This harness proves that restoring the Eagle WebSocket connection is not
enough to make BTS reconnect-ready.

It deliberately creates this sequence:

    SESSION 1
        connect
        receive partial replay through seq 3
        real WebSocket disconnect
        durable cursor remains 3

    SESSION 2
        automatically reconnect with since_seq=3
        receive remaining replay
        receive fresh heartbeat
        replay completes
        heartbeat becomes healthy

        BUT:

        Eagle open-position snapshot = FLAT
        synthetic broker snapshot = LONG 1 MBT

        reconciliation = MISMATCHED
        reconnect readiness = NOT READY

        PASS

This harness contains no broker client, IB code, TradeCoordinator,
TradeRequest, ResumeManager, or order-submission path.
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
from app.reconciliation_manager import (
    ReconciliationManager,
    ReconciliationStatus,
)
from app.reconnect_readiness import ReconnectReadiness
from app.replay_tracker import ReplayTracker


DEFAULT_URI = "ws://localhost:8766"

DEFAULT_DATABASE_PATH = (
    Path("data")
    / "eagle_reconnect_reconciliation_mismatch_test.db"
)

EXPECTED_DISCONNECT_CURSOR = 3
EXPECTED_FINAL_CURSOR = 6

HEARTBEAT_TIMEOUT_SECONDS = 45


SYNTHETIC_BROKER_POSITION = {
    "symbol": "MBT",
    "quantity": 1,
}


@dataclass(frozen=True, slots=True)
class ReconciliationMismatchResult:
    """Immutable result of the negative reconnect safety scenario."""

    initial_cursor: int | None

    first_connection_uri: str
    first_hello_received: bool
    first_replay_expected: int
    first_replay_processed: int
    first_replay_complete: bool

    disconnect_detected: bool
    cursor_after_disconnect: int | None

    second_connection_uri: str
    second_hello_received: bool
    second_requested_since_seq: int | None

    second_replay_expected: int
    second_replay_processed: int
    second_replay_complete: bool

    reconciliation_status: ReconciliationStatus
    reconciliation_matched: bool

    heartbeat_healthy: bool
    reconnect_ready: bool

    final_cursor: int | None

    @property
    def successful(self) -> bool:
        """Return True only when BTS remains fail-closed."""

        return (
            self.initial_cursor is None
            and "since_seq=" not in self.first_connection_uri
            and self.first_hello_received
            and self.first_replay_expected == 3
            and self.first_replay_processed == 2
            and not self.first_replay_complete
            and self.disconnect_detected
            and (
                self.cursor_after_disconnect
                == EXPECTED_DISCONNECT_CURSOR
            )
            and "since_seq=3" in self.second_connection_uri
            and self.second_hello_received
            and self.second_requested_since_seq == 3
            and self.second_replay_expected == 1
            and self.second_replay_processed == 1
            and self.second_replay_complete
            and (
                self.reconciliation_status
                is ReconciliationStatus.MISMATCHED
            )
            and not self.reconciliation_matched
            and self.heartbeat_healthy
            and not self.reconnect_ready
            and self.final_cursor == EXPECTED_FINAL_CURSOR
        )


def build_client(
    *,
    uri: str,
    event_store: EventStore,
) -> EagleClient:
    """Create a fresh EagleClient from durable sequence state."""

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
    """Process lifecycle event with accepted-only replay accounting."""

    replay_was_complete = (
        replay_tracker.replay_complete
    )

    result = event_processor.process(
        event
    )

    if (
        not replay_was_complete
        and result.status
        is EventProcessStatus.ACCEPTED
    ):
        replay_tracker.record_lifecycle_event(
            event
        )

    return result.status


async def run_first_session(
    *,
    client: EagleClient,
    event_store: EventStore,
) -> tuple[
    ReplayTracker,
    bool,
]:
    """Run the deliberately interrupted first WebSocket session."""

    event_processor = EventProcessor(
        event_store
    )

    heartbeat_processor = HeartbeatProcessor(
        event_store
    )

    replay_tracker = ReplayTracker()

    disconnect_detected = False

    print()
    print("=" * 60)
    print(
        "RECONCILIATION MISMATCH TEST - SESSION 1"
    )
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
                replay_tracker.process_hello(
                    message
                )

                print()
                print(
                    "fund.hello received."
                )
                print(
                    f"since_seq: "
                    f"{message.since_seq}"
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
                heartbeat_processor.process(
                    message
                )

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
                status = (
                    process_lifecycle_event(
                        event=message,
                        event_processor=event_processor,
                        replay_tracker=replay_tracker,
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
        print(
            "EAGLE DISCONNECT DETECTED"
        )
        print("=" * 60)

        print(
            f"{type(error).__name__}: "
            f"{error}"
        )

        print(
            f"Durable cursor: "
            f"{event_store.get_last_seq()}"
        )

        print(
            f"Replay complete: "
            f"{replay_tracker.replay_complete}"
        )

        print(
            "BTS remains fail-closed."
        )

    return (
        replay_tracker,
        disconnect_detected,
    )


async def run_recovery_session(
    *,
    client: EagleClient,
    event_store: EventStore,
) -> tuple[
    ReplayTracker,
    ReconciliationManager,
    ConnectionHealth,
    int | None,
]:
    """Reconnect while deliberately forcing reconciliation mismatch."""

    event_processor = EventProcessor(
        event_store
    )

    heartbeat_processor = HeartbeatProcessor(
        event_store
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

    requested_since_seq: int | None = None

    print()
    print("=" * 60)
    print(
        "RECONCILIATION MISMATCH TEST - SESSION 2"
    )
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
                    broker_positions=[
                        SYNTHETIC_BROKER_POSITION
                    ],
                )
            )

            print()
            print(
                "fund.hello received."
            )

            print(
                f"since_seq: "
                f"{message.since_seq}"
            )

            print(
                f"replay_count: "
                f"{message.replay_count}"
            )

            print()
            print(
                "INTENTIONAL POSITION MISMATCH"
            )

            print(
                f"Eagle positions: "
                f"{len(message.open_positions)}"
            )

            print(
                "Synthetic broker positions: 1"
            )

            print(
                f"Reconciliation status: "
                f"{reconciliation_result.status.value}"
            )

        elif isinstance(
            message,
            EagleHeartbeat,
        ):
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
                f"Heartbeat healthy: "
                f"{health.is_healthy()}"
            )

            print(
                f"Durable cursor: "
                f"{event_store.get_last_seq()}"
            )

        elif isinstance(
            message,
            IncomingLifecycleEvent,
        ):
            status = (
                process_lifecycle_event(
                    event=message,
                    event_processor=event_processor,
                    replay_tracker=replay_tracker,
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
            and health.is_healthy()
            and event_store.get_last_seq()
            == EXPECTED_FINAL_CURSOR
        ):
            print()
            print(
                "Replay and heartbeat recovery completed."
            )

            print(
                f"Final reconciliation: "
                f"{reconciliation_manager.last_result.status.value}"
            )

            print(
                f"Reconnect readiness: "
                f"{readiness.status.value}"
            )

            break

    return (
        replay_tracker,
        reconciliation_manager,
        health,
        requested_since_seq,
    )


async def run_reconciliation_mismatch_test(
    *,
    uri: str,
    database_path: str | Path,
) -> ReconciliationMismatchResult:
    """Run the complete negative reconnect scenario."""

    event_store = EventStore(
        database_path
    )

    initial_cursor = (
        event_store.get_last_seq()
    )

    if initial_cursor is not None:
        raise RuntimeError(
            "Reconciliation mismatch test requires "
            "a fresh event database. "
            f"Existing durable cursor: "
            f"{initial_cursor}."
        )

    first_client = build_client(
        uri=uri,
        event_store=event_store,
    )

    first_connection_uri = (
        first_client._connection_uri()
    )

    (
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
            "Expected Eagle disconnect "
            "was not detected."
        )

    if (
        cursor_after_disconnect
        != EXPECTED_DISCONNECT_CURSOR
    ):
        raise RuntimeError(
            "Unexpected durable cursor after "
            "the interrupted first session."
        )

    if first_replay_tracker.replay_complete:
        raise RuntimeError(
            "Interrupted replay was incorrectly "
            "marked complete."
        )

    print()
    print(
        "Automatically reconnecting from "
        "the durable cursor..."
    )

    second_client = build_client(
        uri=uri,
        event_store=event_store,
    )

    second_connection_uri = (
        second_client._connection_uri()
    )

    (
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

    result = ReconciliationMismatchResult(
        initial_cursor=initial_cursor,

        first_connection_uri=(
            first_connection_uri
        ),

        first_hello_received=(
            first_replay_tracker.hello_received
        ),

        first_replay_expected=(
            first_replay_tracker.expected_replay_count
        ),

        first_replay_processed=(
            first_replay_tracker.processed_replay_count
        ),

        first_replay_complete=(
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

        second_replay_expected=(
            second_replay_tracker.expected_replay_count
        ),

        second_replay_processed=(
            second_replay_tracker.processed_replay_count
        ),

        second_replay_complete=(
            second_replay_tracker.replay_complete
        ),

        reconciliation_status=(
            reconciliation_manager
            .last_result
            .status
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
    result: ReconciliationMismatchResult,
) -> None:
    """Print the negative reconnect test summary."""

    if not isinstance(
        result,
        ReconciliationMismatchResult,
    ):
        raise TypeError(
            "'result' must be a "
            "ReconciliationMismatchResult."
        )

    print()
    print(
        "BTS / EAGLE RECONNECT RECONCILIATION "
        "MISMATCH TEST"
    )

    print("=" * 60)

    print(
        f"Initial cursor:                 "
        f"{result.initial_cursor}"
    )

    print(
        f"First replay expected:          "
        f"{result.first_replay_expected}"
    )

    print(
        f"First replay processed:         "
        f"{result.first_replay_processed}"
    )

    print(
        f"First replay complete:          "
        f"{result.first_replay_complete}"
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
        f"{result.second_replay_expected}"
    )

    print(
        f"Second replay processed:        "
        f"{result.second_replay_processed}"
    )

    print(
        f"Second replay complete:         "
        f"{result.second_replay_complete}"
    )

    print(
        f"Reconciliation status:          "
        f"{result.reconciliation_status.value}"
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
            "RESULT: PASS - BTS remained fail-closed "
            "after reconnect because position "
            "reconciliation was mismatched."
        )

    else:
        print(
            "RESULT: FAIL - reconciliation mismatch "
            "did not preserve fail-closed recovery."
        )

    print(
        "NO BROKER OR ORDER-SUBMISSION PATH EXISTS."
    )

    print()


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run the BTS Eagle reconnect "
            "reconciliation-mismatch safety test."
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
            "the mismatch test."
        ),
    )

    return parser.parse_args()


async def async_main(
    arguments: argparse.Namespace,
) -> ReconciliationMismatchResult:
    """Run the negative reconnect test."""

    print()
    print(
        "Starting Eagle reconnect "
        "reconciliation-mismatch test..."
    )

    print(
        f"Eagle URI: "
        f"{arguments.uri}"
    )

    print(
        f"Database: "
        f"{arguments.database}"
    )

    print(
        "NO BROKER OR ORDER-SUBMISSION PATH EXISTS."
    )

    result = (
        await run_reconciliation_mismatch_test(
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
    """Run the reconciliation-mismatch harness."""

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
            "Reconciliation mismatch test "
            "stopped by operator."
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