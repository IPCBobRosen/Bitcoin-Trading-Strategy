"""Run the local Eagle-to-TradeRequest integration test."""

import asyncio

from app.communications.eagle_client import EagleClient
from app.communications.trade_request import TradeRequest
from app.trading_controls import TradingControls


EAGLE_URI = "ws://localhost:8765"


async def main() -> None:
    """Continuously receive Eagle signals and process each one."""

    print("=" * 60)
    print("Bitcoin Trading System - Local Eagle Client Test")
    print("=" * 60)

    controls = TradingControls(
        symbol="MBT",
        quantity=1,
        stop_loss_points=500,
    )

    print(f"Eagle address : {EAGLE_URI}")
    print(f"Trading paused: {controls.is_paused}")
    print(f"Symbol        : {controls.symbol}")
    print(f"Quantity      : {controls.quantity}")
    print(f"Stop loss     : {controls.stop_loss_points}")
    print()

    client = EagleClient(EAGLE_URI)

    print("Connecting to fake Eagle server...")
    print("Listening for lifecycle events...")
    print()

    event_count = 0

    async for event in client.listen():
        event_count += 1

        print("-" * 60)
        print(f"Lifecycle event #{event_count} received and validated:")
        print(event)

        if controls.is_paused:
            print()
            print("Trading is PAUSED.")
            print("No TradeRequest was created.")
            print()
            continue

        settings = controls.create_snapshot()
        request = TradeRequest.from_event(event, settings)

        print()
        print("TradeRequest created:")
        print(request)
        print()

    print("-" * 60)
    print(f"Eagle connection closed after {event_count} event(s).")


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