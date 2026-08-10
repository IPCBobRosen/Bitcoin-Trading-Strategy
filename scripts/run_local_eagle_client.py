"""Run the local Eagle-to-BTS integration test."""

import asyncio
from pathlib import Path

from app.communications.eagle_client import EagleClient
from app.communications.eagle_heartbeat import EagleHeartbeat
from app.communications.eagle_hello import EagleHello
from app.communications.incoming_event import IncomingLifecycleEvent
from app.connection_health import ConnectionHealth
from app.event_processor import EventProcessStatus, EventProcessor
from app.event_store import EventStore
from app.heartbeat_processor import HeartbeatProcessor
from app.heartbeat_watchdog import HeartbeatWatchdog
from app.replay_tracker import ReplayTracker
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


EAGLE_URI = "ws://localhost:8765"

DATABASE_PATH = Path("data") / "local_eagle_events.db"

HEARTBEAT_TIMEOUT_SECONDS = 45
WATCHDOG_CHECK_INTERVAL_SECONDS = 1.0
INITIAL_HEARTBEAT_TIMEOUT_SECONDS = 45.0


async def main() -> None:
    """Receive Eagle messages while monitoring connection health."""

    print("=" * 60)
    print("Bitcoin Trading System - Local Eagle Client Test")
    print("=" * 60)

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    event_store = EventStore(
        DATABASE_PATH
    )

    event_processor = EventProcessor(
        event_store
    )

    heartbeat_processor = HeartbeatProcessor(
        event_store
    )

    replay_tracker = ReplayTracker()

    health = ConnectionHealth(
        heartbeat_timeout_seconds=HEARTBEAT_TIMEOUT_SECONDS
    )

    watchdog = HeartbeatWatchdog(
        health,
        check_interval_seconds=WATCHDOG_CHECK_INTERVAL_SECONDS,
        initial_heartbeat_timeout_seconds=(
            INITIAL_HEARTBEAT_TIMEOUT_SECONDS
        ),
    )

    coordinator = TradeCoordinator(
        controls
    )

    last_durable_seq = event_store.get_last_seq()

    client = EagleClient(
        EAGLE_URI,
        since_seq=last_durable_seq,
    )

    print(f"Eagle address    : {EAGLE_URI}")
    print(f"Trading paused   : {controls.is_paused}")
    print(f"Symbol           : {controls.symbol}")
    print(f"Quantity         : {controls.quantity}")
    print(f"Stop loss        : {controls.stop_loss_points}")
    print(f"Event database   : {DATABASE_PATH}")
    print(f"Last durable seq : {last_durable_seq}")
    print(f"Reconnect cursor : {last_durable_seq}")

    print(
        f"Heartbeat timeout: "
        f"{health.heartbeat_timeout_seconds} seconds"
    )

    print()
    print("Connecting to fake Eagle server...")
    print("Listening for Eagle messages...")
    print("Heartbeat watchdog started.")
    print()

    message_count = 0
    lifecycle_count = 0
    heartbeat_count = 0
    hello_count = 0

    async def process_eagle_messages() -> None:
        """Receive and route messages from Eagle."""

        nonlocal message_count
        nonlocal lifecycle_count
        nonlocal heartbeat_count
        nonlocal hello_count

        async for message in client.listen():
            message_count += 1

            print("-" * 60)

            if isinstance(message, EagleHello):
                hello_count += 1

                replay_tracker.process_hello(
                    message
                )

                print(
                    f"Eagle hello #{hello_count} "
                    "received and validated:"
                )

                print(message)

                print()
                print("Connection snapshot:")

                print(
                    f"Server last seq : "
                    f"{message.last_seq}"
                )

                print(
                    f"Requested since : "
                    f"{message.since_seq}"
                )

                print(
                    f"Replay count    : "
                    f"{message.replay_count}"
                )

                print(
                    f"Open positions  : "
                    f"{message.open_count}"
                )

                print(
                    f"Environment     : "
                    f"{message.environment.value}"
                )

                print()
                print("Replay tracking:")

                print(
                    f"Expected replay : "
                    f"{replay_tracker.expected_replay_count}"
                )

                print(
                    f"Processed replay: "
                    f"{replay_tracker.processed_replay_count}"
                )

                print(
                    f"Replay complete : "
                    f"{replay_tracker.replay_complete}"
                )

                print()
                print(
                    "fund.hello is a control frame. "
                    "No TradeRequest will be created."
                )

                if replay_tracker.replay_complete:
                    print()
                    print(
                        "Eagle announced no lifecycle replay events."
                    )
                    print(
                        "Replay phase is COMPLETE."
                    )

                print()
                continue

            if isinstance(
                message,
                EagleHeartbeat,
            ):
                heartbeat_count += 1

                print(
                    f"Heartbeat #{heartbeat_count} "
                    "received and validated:"
                )

                print(message)

                heartbeat_processor.process(
                    message
                )

                health.record_heartbeat()

                print()

                print(
                    "Heartbeat sequence persisted:",
                    message.seq,
                )

                print(
                    "Last durable seq:",
                    event_store.get_last_seq(),
                )

                print(
                    "Eagle healthy:",
                    health.is_healthy(),
                )

                print(
                    "Heartbeat age:",
                    health.heartbeat_age_seconds(),
                )

                print(
                    "Replay progress:",
                    (
                        f"{replay_tracker.processed_replay_count}"
                        f"/"
                        f"{replay_tracker.expected_replay_count}"
                    ),
                )

                print(
                    "Heartbeat is a control frame. "
                    "It does not count as a replay lifecycle event "
                    "and no TradeRequest will be created."
                )

                print()
                continue

            if isinstance(
                message,
                IncomingLifecycleEvent,
            ):
                lifecycle_count += 1

                print(
                    f"Lifecycle event "
                    f"#{lifecycle_count} "
                    "received and validated:"
                )

                print(message)

                replay_was_complete = (
                    replay_tracker.replay_complete
                )

                replay_tracker.record_lifecycle_event(
                    message
                )

                if not replay_was_complete:
                    print()
                    print(
                        "Replay lifecycle progress:",
                        (
                            f"{replay_tracker.processed_replay_count}"
                            f"/"
                            f"{replay_tracker.expected_replay_count}"
                        ),
                    )

                    if replay_tracker.replay_complete:
                        print(
                            "Replay phase is COMPLETE."
                        )

                process_result = (
                    event_processor.process(
                        message
                    )
                )

                print()

                print(
                    "Event processing:",
                    process_result.status.value,
                )

                if (
                    process_result.status
                    is not EventProcessStatus.ACCEPTED
                ):
                    print(
                        "Event will not continue "
                        "to TradeCoordinator."
                    )

                    print()
                    continue

                decision = coordinator.process_event(
                    message
                )

                print()

                print(
                    "Trade decision:",
                    f"approved={decision.approved},",
                    f"reason={decision.reason}",
                )

                if (
                    decision.trade_request
                    is not None
                ):
                    print()
                    print("TradeRequest created:")
                    print(
                        decision.trade_request
                    )

                else:
                    print()
                    print(
                        "No TradeRequest was created."
                    )

                print()
                continue

            raise RuntimeError(
                "Unsupported Eagle message type: "
                f"{type(message).__name__}"
            )

    async def handle_heartbeat_timeout() -> None:
        """Apply the immediate trading safety action."""

        controls.pause()

    def print_heartbeat_timeout_warning() -> None:
        """Display the heartbeat-loss safety condition."""

        heartbeat_age = (
            health.heartbeat_age_seconds()
        )

        print()
        print("=" * 60)
        print("EAGLE HEARTBEAT TIMEOUT")
        print("=" * 60)

        print(
            "Eagle connection is considered unavailable."
        )

        if heartbeat_age is not None:
            print(
                f"Last heartbeat age: "
                f"{heartbeat_age:.2f} seconds."
            )

        else:
            print(
                "No Eagle heartbeat was received "
                "within the startup grace period."
            )

        print(
            "BTS trading has been PAUSED."
        )

        print(
            "No new TradeRequests may be approved."
        )

        print(
            "The Eagle listener will now be stopped."
        )

        print(
            f"Last durable seq: "
            f"{event_store.get_last_seq()}"
        )

        print("=" * 60)
        print()

    listener_task = asyncio.create_task(
        process_eagle_messages(),
        name="eagle-listener",
    )

    watchdog_task = asyncio.create_task(
        watchdog.monitor(
            handle_heartbeat_timeout
        ),
        name="heartbeat-watchdog",
    )

    done_tasks, _ = await asyncio.wait(
        {
            listener_task,
            watchdog_task,
        },
        return_when=asyncio.FIRST_COMPLETED,
    )

    if watchdog_task in done_tasks:
        await watchdog_task

        controls.pause()

        print_heartbeat_timeout_warning()

        if not listener_task.done():
            listener_task.cancel()

            try:
                await listener_task

            except asyncio.CancelledError:
                pass

    else:
        await listener_task

        if not watchdog_task.done():
            watchdog_task.cancel()

            try:
                await watchdog_task

            except asyncio.CancelledError:
                pass

    print("-" * 60)

    print(
        f"Eagle session ended after "
        f"{message_count} total message(s)."
    )

    print(
        f"Hello frames     : "
        f"{hello_count}"
    )

    print(
        f"Heartbeats       : "
        f"{heartbeat_count}"
    )

    print(
        f"Lifecycle events : "
        f"{lifecycle_count}"
    )

    print(
        f"Replay expected  : "
        f"{replay_tracker.expected_replay_count}"
    )

    print(
        f"Replay processed : "
        f"{replay_tracker.processed_replay_count}"
    )

    print(
        f"Replay complete  : "
        f"{replay_tracker.replay_complete}"
    )

    print(
        f"Last durable seq : "
        f"{event_store.get_last_seq()}"
    )

    print(
        f"Trading paused   : "
        f"{controls.is_paused}"
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except ConnectionRefusedError:
        print()
        print("Connection failed.")
        print(
            "Start the fake Eagle server first."
        )

    except ConnectionError as error:
        print()
        print(
            f"Connection error: {error}"
        )

    except ValueError as error:
        print()
        print(
            f"Invalid Eagle message: {error}"
        )

    except KeyboardInterrupt:
        print()
        print("BTS client stopped.")