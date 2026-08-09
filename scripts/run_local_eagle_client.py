"""Run the local Eagle-to-BTS integration test."""

import asyncio
from pathlib import Path

from app.communications.eagle_client import EagleClient
from app.event_processor import EventProcessStatus, EventProcessor
from app.event_store import EventStore
from app.trade_coordinator import TradeCoordinator
from app.trading_controls import TradingControls


EAGLE_URI = "ws://localhost:8765"

DATABASE_PATH = Path("data") / "local_eagle_events.db"


async def main() -> None:
    """Continuously receive and process Eagle lifecycle events."""

    print("=" * 60)
    print("Bitcoin Trading System - Local Eagle Client Test")
    print("=" * 60)

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    event_store = EventStore(DATABASE_PATH)
    event_processor = EventProcessor(event_store)
    coordinator = TradeCoordinator(controls)

    print(f"Eagle address  : {EAGLE_URI}")
    print(f"Trading paused : {controls.is_paused}")
    print(f"Symbol         : {controls.symbol}")
    print(f"Quantity       : {controls.quantity}")
    print(f"Stop loss      : {controls.stop_loss_points}")
    print(f"Event database : {DATABASE_PATH}")
    print()

    client = EagleClient(EAGLE_URI)

    print("Connecting to fake Eagle server...")
    print("Listening for lifecycle events...")
    print()

    event_count = 0

    async for event in client.listen():
        event_count += 1

        print("-" * 60)
        print(
            f"Lifecycle event #{event_count} "
            "received and validated:"
        )
        print(event)

        process_result = event_processor.process(event)

        print()
        print(
            "Event processing:",
            process_result.status.value,
        )

        if process_result.status is not EventProcessStatus.ACCEPTED:
            print("Event will not continue to TradeCoordinator.")
            print()
            continue

        decision = coordinator.process_event(event)

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

    print("-" * 60)
    print(
        f"Eagle connection closed after "
        f"{event_count} event(s)."
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