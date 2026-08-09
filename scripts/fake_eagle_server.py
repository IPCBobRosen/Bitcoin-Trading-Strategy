"""Local fake Eagle WebSocket server for BTS integration testing."""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from websockets.asyncio.server import ServerConnection, serve


HOST = "localhost"
PORT = 8765
MESSAGE_DELAY_SECONDS = 2


def current_timestamp() -> str:
    """Return the current UTC timestamp in ISO-8601 format."""

    return datetime.now(timezone.utc).isoformat()


def create_hello() -> dict[str, Any]:
    """Create a realistic Eagle fund.hello control frame."""

    return {
        "type": "fund.hello",
        "contract": "1.2.0",
        "version": "1.2.0",
        "capabilities": [
            "size_mult",
            "fund.add",
            "funding_rate",
            "p14_disable",
        ],
        "flags": {
            "p14_disabled": True,
            "size_mult_enabled": False,
            "pyramid_enabled": False,
        },
        "last_seq": 5,
        "since_seq": 0,
        "open_count": 0,
        "open": [],
        "open_by_channel": {
            "fund": [],
            "apollo": [],
            "hermes": [],
            "athena": [],
            "moab": [],
        },
        "replay_count": 0,
        "ts": current_timestamp(),
        "env": "staging",
    }


def create_heartbeat(
    *,
    seq: int,
) -> dict[str, Any]:
    """Create a realistic Eagle fund.heartbeat control frame."""

    return {
        "type": "fund.heartbeat",
        "seq": seq,
        "open_count": 0,
        "open_count_by_channel": {
            "fund": 0,
            "apollo": 0,
            "hermes": 0,
            "athena": 0,
            "moab": 0,
        },
    }


def create_lifecycle_event(
    *,
    message_type: str,
    seq: int,
    event_id: str,
    signal_id: str,
    intent: str,
) -> dict[str, Any]:
    """Create one realistic Eagle lifecycle-event message."""

    return {
        "type": message_type,
        "seq": seq,
        "event_id": event_id,
        "signal_id": signal_id,
        "ts": current_timestamp(),
        "env": "staging",
        "payload": {
            "intent": intent,
        },
    }


async def send_message(
    websocket: ServerConnection,
    message: dict[str, Any],
    description: str,
) -> None:
    """Send one JSON message to the connected BTS client."""

    await websocket.send(
        json.dumps(message)
    )

    print(description)

    await asyncio.sleep(
        MESSAGE_DELAY_SECONDS
    )


async def handle_client(
    websocket: ServerConnection,
) -> None:
    """Send a realistic Eagle message sequence to one BTS client."""

    print("BTS client connected.")

    try:
        hello = create_hello()

        await send_message(
            websocket,
            hello,
            "Sent fund.hello control frame.",
        )

        entry_long = create_lifecycle_event(
            message_type="fund.entry",
            seq=1,
            event_id="local-event-001",
            signal_id="local-signal-001",
            intent="BUY_TO_OPEN",
        )

        await send_message(
            websocket,
            entry_long,
            "Sent seq 1: fund.entry / BUY_TO_OPEN",
        )

        heartbeat_1 = create_heartbeat(
            seq=2,
        )

        await send_message(
            websocket,
            heartbeat_1,
            "Sent seq 2: fund.heartbeat",
        )

        entry_short = create_lifecycle_event(
            message_type="fund.entry",
            seq=3,
            event_id="local-event-003",
            signal_id="local-signal-003",
            intent="SELL_TO_OPEN",
        )

        await send_message(
            websocket,
            entry_short,
            "Sent seq 3: fund.entry / SELL_TO_OPEN",
        )

        heartbeat_2 = create_heartbeat(
            seq=4,
        )

        await send_message(
            websocket,
            heartbeat_2,
            "Sent seq 4: fund.heartbeat",
        )

        exit_event = create_lifecycle_event(
            message_type="fund.exit",
            seq=5,
            event_id="local-event-005",
            signal_id="local-signal-001",
            intent="SELL_TO_CLOSE",
        )

        await send_message(
            websocket,
            exit_event,
            "Sent seq 5: fund.exit / SELL_TO_CLOSE",
        )

        print("All test messages sent.")
        print("Closing this client connection.")

    except ConnectionError as error:
        print(
            f"BTS client connection ended early: {error}"
        )


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
        print()
        print("Fake Eagle server stopped.")