"""Run the local Eagle-to-BTS integration test."""

import asyncio
from pathlib import Path

from app.communications.eagle_client import EagleClient
from app.communications.eagle_heartbeat import EagleHeartbeat
from app.communications.eagle_hello import EagleHello
from app.communications.incoming_event import IncomingLifecycleEvent
from app.event_processor import EventProcessStatus, EventProcessor
from app.event_store import EventStore
from app.heartbeat_processor import HeartbeatProcessor
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


EAGLE_URI = "ws://localhost:8765"

DATABASE_PATH = Path("data") / "local_eagle_events.db"


async def main() -> None:
    """Continuously receive and route validated Eagle messages."""

    print("=" * 60)
    print("Bitcoin Trading System - Local Eagle Client Test")
    print("=" * 60)

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    event_store = EventStore(DATABASE_PATH)

    event_processor = EventProcessor(
        event_store
    )

    heartbeat_processor = HeartbeatProcessor(
        event_store
    )

    coordinator = TradeCoordinator(
        controls
    )

    print(f"Eagle address  : {EAGLE_URI}")
    print(f"Trading paused : {controls.is_paused}")
    print(f"Symbol         : {controls.symbol}")
    print(f"Quantity       : {controls.quantity}")
    print(f"Stop loss      : {controls.stop_loss_points}")
    print(f"Event database : {DATABASE_PATH}")
    print(f"Last durable seq: {event_store.get_last_seq()}")
    print()

    client = EagleClient(EAGLE_URI)

    print("Connecting to fake Eagle server...")
    print("Listening for Eagle messages...")
    print()

    message_count = 0
    lifecycle_count = 0
    heartbeat_count = 0
    hello_count = 0

    async for message in client.listen():
        message_count += 1

        print("-" * 60)

        if isinstance(message, EagleHello):
            hello_count += 1

            print(
                f"Eagle hello #{hello_count} "
                "received and validated:"
            )
            print(message)

            print()
            print("Connection snapshot:")
            print(f"Server last seq : {message.last_seq}")
            print(f"Requested since : {message.since_seq}")
            print(f"Replay count    : {message.replay_count}")
            print(f"Open positions  : {message.open_count}")
            print(f"Environment     : {message.environment.value}")

            print()
            print(
                "fund.hello is a control frame. "
                "No TradeRequest will be created."
            )

            print()
            continue

        if isinstance(message, EagleHeartbeat):
            heartbeat_count += 1

            print(
                f"Heartbeat #{heartbeat_count} "
                "received and validated:"
            )
            print(message)

            heartbeat_processor.process(message)

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
                "Heartbeat is a control frame. "
                "No TradeRequest will be created."
            )

            print()
            continue

        if isinstance(message, IncomingLifecycleEvent):
            lifecycle_count += 1

            print(
                f"Lifecycle event #{lifecycle_count} "
                "received and validated:"
            )
            print(message)

            process_result = event_processor.process(
                message
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

            if decision.trade_request is not None:
                print()
                print("TradeRequest created:")
                print(decision.trade_request)

            else:
                print()
                print("No TradeRequest was created.")

            print()
            continue

        raise RuntimeError(
            f"Unsupported Eagle message type: "
            f"{type(message).__name__}"
        )

    print("-" * 60)

    print(
        f"Eagle connection closed after "
        f"{message_count} total message(s)."
    )

    print(f"Hello frames     : {hello_count}")
    print(f"Heartbeats       : {heartbeat_count}")
    print(f"Lifecycle events : {lifecycle_count}")
    print(
        f"Last durable seq : "
        f"{event_store.get_last_seq()}"
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except ConnectionRefusedError:
        print()
        print("Connection failed.")
        print("Start the fake Eagle server first.")

    except ConnectionError as error:
        print()
        print(f"Connection error: {error}")

    except ValueError as error:
        print()
        print(f"Invalid Eagle message: {error}")

    except KeyboardInterrupt:
        print()
        print("BTS client stopped.")