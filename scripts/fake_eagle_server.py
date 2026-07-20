"""Local fake Eagle WebSocket server for BTS integration testing."""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from websockets.asyncio.server import ServerConnection, serve


HOST = "localhost"
PORT = 8765
MESSAGE_DELAY_SECONDS = 2


def create_event(
    *,
    seq: int,
    intent: str,
) -> dict[str, Any]:
    """Create one realistic Eagle lifecycle-event message."""

    return {
        "type": "fund.entry",
        "seq": seq,
        "event_id": f"local-event-{seq:03d}",
        "signal_id": f"local-signal-{seq:03d}",
        "ts": datetime.now(timezone.utc).isoformat(),
        "env": "staging",
        "payload": {
            "intent": intent,
        },
    }


async def handle_client(websocket: ServerConnection) -> None:
    """Send several lifecycle events to one connected BTS client."""

    print("BTS client connected.")

    intents = [
        "BUY_TO_OPEN",
        "SELL_TO_OPEN",
        "BUY_TO_CLOSE",
        "SELL_TO_CLOSE",
        "BUY_TO_OPEN",
    ]

    try:
        for seq, intent in enumerate(intents, start=1):
            event = create_event(
                seq=seq,
                intent=intent,
            )

            await websocket.send(json.dumps(event))

            print(
                f"Sent event {seq}: "
                f"{event['event_id']} / {intent}"
            )

            await asyncio.sleep(MESSAGE_DELAY_SECONDS)

        print("All test events sent.")
        print("Closing this client connection.")

    except ConnectionError as error:
        print(f"BTS client connection ended early: {error}")


async def main() -> None:
    """Start the fake Eagle WebSocket server."""

    print("=" * 60)
    print("Fake Eagle WebSocket Server")
    print("=" * 60)
    print(f"Listening at ws://{HOST}:{PORT}")
    print("Waiting for the BTS client...")
    print("Press Ctrl+C to stop the server.")

    async with serve(
        handle_client,
        HOST,
        PORT,
        ping_interval=20,
        ping_timeout=20,
        max_size=1_048_576,
    ):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nFake Eagle server stopped.")